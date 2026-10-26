"""tests for resumable network volume transfers against an in-process fake s3."""

import hashlib
import io
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from click.testing import CliRunner

import runpod
import runpod.network_volume_transfer as nvt
import runpod.error


def _client_error(code, status, op="Op"):
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, op
    )


class FakeS3:
    """the subset of the s3 api the transfer module uses."""

    def __init__(self, md5_etags=True):
        self.objects = {}  # key -> (data, last_modified)
        self.multipart_etags = {}  # key -> etag of an object assembled from parts
        self.uploads = {}  # upload_id -> {"key": str, "parts": {n: bytes}}
        self.calls = []
        self.fail = None  # fail(op, kwargs) -> exception or None
        self.md5_etags = md5_etags
        self._next = 0

    def _etag(self, data):
        if self.md5_etags:
            return '"' + hashlib.md5(data).hexdigest() + '"'
        return '"opaque-' + str(len(data)) + '"'

    def _call(self, op, **kwargs):
        self.calls.append((op, kwargs))
        if self.fail is not None:
            exc = self.fail(op, kwargs)
            if exc is not None:
                raise exc

    def put(self, key, data, modified=None):
        self.objects[key] = (data, modified or datetime.now(timezone.utc))
        self.multipart_etags.pop(key, None)

    def object_etag(self, key):
        return self.multipart_etags.get(key) or self._etag(self.objects[key][0])

    # -- objects

    def put_object(self, Bucket, Key, Body):
        self._call("put_object", Key=Key)
        self.put(Key, bytes(Body))
        return {"ETag": self._etag(bytes(Body))}

    def head_object(self, Bucket, Key):
        self._call("head_object", Key=Key)
        if Key not in self.objects:
            raise _client_error("404", 404, "HeadObject")
        data, modified = self.objects[Key]
        return {"ContentLength": len(data), "ETag": self.object_etag(Key), "LastModified": modified}

    def get_object(self, Bucket, Key, Range):
        self._call("get_object", Key=Key, Range=Range)
        data, _ = self.objects[Key]
        start, end = (int(x) for x in Range.split("=")[1].split("-"))
        return {"Body": io.BytesIO(data[start : end + 1]), "ETag": self.object_etag(Key)}

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        self._call("list_objects_v2", Prefix=Prefix, ContinuationToken=ContinuationToken)
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start : start + 2]
        truncated = start + 2 < len(keys)
        return {
            "Contents": [
                {
                    "Key": k,
                    "Size": len(self.objects[k][0]),
                    "ETag": self.object_etag(k),
                    "LastModified": self.objects[k][1],
                }
                for k in page
            ],
            "IsTruncated": truncated,
            "NextContinuationToken": str(start + 2) if truncated else None,
        }

    # -- multipart

    def create_multipart_upload(self, Bucket, Key):
        self._call("create_multipart_upload", Key=Key)
        self._next += 1
        upload_id = f"up-{self._next}"
        self.uploads[upload_id] = {"key": Key, "parts": {}}
        return {"UploadId": upload_id}

    def _upload(self, upload_id):
        if upload_id not in self.uploads:
            raise _client_error("NoSuchUpload", 404, "Multipart")
        return self.uploads[upload_id]

    def upload_part(self, Bucket, Key, UploadId, PartNumber, Body):
        self._call("upload_part", PartNumber=PartNumber)
        self._upload(UploadId)["parts"][PartNumber] = bytes(Body)
        return {"ETag": self._etag(bytes(Body))}

    def list_parts(self, Bucket, Key, UploadId, PartNumberMarker=0):
        self._call("list_parts", UploadId=UploadId, PartNumberMarker=PartNumberMarker)
        parts = sorted(self._upload(UploadId)["parts"].items())
        parts = [p for p in parts if p[0] > int(PartNumberMarker)]
        page, rest = parts[:2], parts[2:]
        return {
            "Parts": [
                {"PartNumber": n, "Size": len(d), "ETag": self._etag(d)} for n, d in page
            ],
            "IsTruncated": bool(rest),
            "NextPartNumberMarker": page[-1][0] if rest else None,
        }

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        self._call("complete_multipart_upload", UploadId=UploadId)
        upload = self._upload(UploadId)
        chunks = [upload["parts"][p["PartNumber"]] for p in MultipartUpload["Parts"]]
        self.put(Key, b"".join(chunks))
        if self.md5_etags:
            digests = b"".join(hashlib.md5(c).digest() for c in chunks)
            self.multipart_etags[Key] = f'"{hashlib.md5(digests).hexdigest()}-{len(chunks)}"'
        del self.uploads[UploadId]
        return {}

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self._call("abort_multipart_upload", UploadId=UploadId)
        self.uploads.pop(UploadId, None)

    def ops(self, op):
        return [kwargs for name, kwargs in self.calls if name == op]


@pytest.fixture(autouse=True)
def small_sizes(monkeypatch):
    monkeypatch.setattr(nvt, "_sleep", lambda _: None)
    monkeypatch.setattr(nvt, "SINGLE_PUT_MAX", 10)
    monkeypatch.setattr(nvt, "DEFAULT_PART_SIZE", 8)
    monkeypatch.setattr(nvt, "DOWNLOAD_CHUNK_SIZE", 8)


def _transfer(fake, tmp_path, concurrency=1, events=None):
    progress = None if events is None else (lambda e, p, n: events.append((e, p, n)))
    return nvt._Transfer(
        fake, "vol", "https://s3", concurrency, progress, state_dir=tmp_path / "state"
    )


DATA = bytes(range(40))  # 5 parts / chunks of 8 bytes


# ---------------------------------------------------------------- upload


def test_part_size_for_limits():
    assert nvt.part_size_for(10) == 8
    size = 4 * 1024**4
    part = nvt.part_size_for(size)
    assert part <= nvt.MAX_PART_SIZE and size / part <= nvt.MAX_PARTS
    with pytest.raises(nvt.NetworkVolumeTransferError):
        nvt.part_size_for(nvt.MAX_PART_SIZE * nvt.MAX_PARTS + 1)


def test_small_file_uses_put(tmp_path):
    fake = FakeS3()
    src = tmp_path / "small.bin"
    src.write_bytes(b"hello")
    summary = _transfer(fake, tmp_path).upload(src, "dir/")
    assert fake.objects["dir/small.bin"][0] == b"hello"
    assert summary == {"files_transferred": 1, "files_skipped": 0, "bytes_transferred": 5}


def test_multipart_resume_uploads_only_missing_and_bad_parts(tmp_path):
    fake = FakeS3()
    src = tmp_path / "big.bin"
    src.write_bytes(DATA)

    fake.fail = lambda op, kw: (
        _client_error("AccessDenied", 403)
        if op == "upload_part" and kw["PartNumber"] == 5
        else None
    )
    with pytest.raises(ClientError):
        _transfer(fake, tmp_path).upload(src, "big.bin")
    assert len(list((tmp_path / "state").glob("*.json"))) == 1
    upload = next(iter(fake.uploads.values()))
    assert sorted(upload["parts"]) == [1, 2, 3, 4]
    upload["parts"][2] = b"X" * 8  # same size, wrong content

    fake.fail = None
    fake.calls.clear()
    events = []
    summary = _transfer(fake, tmp_path, events=events).upload(src, "big.bin")

    assert sorted(kw["PartNumber"] for kw in fake.ops("upload_part")) == [2, 5]
    assert not fake.ops("create_multipart_upload")
    assert fake.objects["big.bin"][0] == DATA
    assert summary["bytes_transferred"] == len(DATA)
    assert not list((tmp_path / "state").glob("*.json"))


def test_multipart_resume_reuploads_parts_without_md5_etag(tmp_path):
    fake = FakeS3(md5_etags=False)
    src = tmp_path / "big.bin"
    src.write_bytes(DATA)
    fake.fail = lambda op, kw: (
        _client_error("AccessDenied", 403)
        if op == "upload_part" and kw["PartNumber"] == 5
        else None
    )
    with pytest.raises(ClientError):
        _transfer(fake, tmp_path).upload(src, "big.bin")
    fake.fail = None
    fake.calls.clear()
    _transfer(fake, tmp_path).upload(src, "big.bin")
    # parts whose content cannot be verified are sent again
    assert sorted(kw["PartNumber"] for kw in fake.ops("upload_part")) == [1, 2, 3, 4, 5]
    assert fake.objects["big.bin"][0] == DATA


def test_changed_file_aborts_stale_upload(tmp_path):
    fake = FakeS3()
    src = tmp_path / "big.bin"
    src.write_bytes(DATA)
    fake.fail = lambda op, kw: (
        _client_error("AccessDenied", 403) if op == "upload_part" and kw["PartNumber"] == 2 else None
    )
    with pytest.raises(ClientError):
        _transfer(fake, tmp_path).upload(src, "big.bin")
    fake.fail = None
    src.write_bytes(DATA[::-1])
    os.utime(src, ns=(1, 1))
    fake.calls.clear()
    _transfer(fake, tmp_path).upload(src, "big.bin")
    assert len(fake.ops("abort_multipart_upload")) == 1
    assert len(fake.ops("upload_part")) == 5
    assert fake.objects["big.bin"][0] == DATA[::-1]


def test_missing_server_upload_starts_fresh(tmp_path):
    fake = FakeS3()
    src = tmp_path / "big.bin"
    src.write_bytes(DATA)
    fake.fail = lambda op, kw: (
        _client_error("AccessDenied", 403) if op == "upload_part" and kw["PartNumber"] == 2 else None
    )
    with pytest.raises(ClientError):
        _transfer(fake, tmp_path).upload(src, "big.bin")
    fake.fail = None
    fake.uploads.clear()  # server-side cleanup
    fake.calls.clear()
    _transfer(fake, tmp_path).upload(src, "big.bin")
    assert len(fake.ops("create_multipart_upload")) == 1
    assert fake.objects["big.bin"][0] == DATA


def test_complete_timeout_then_head_counts_as_success(tmp_path):
    fake = FakeS3()
    src = tmp_path / "big.bin"
    src.write_bytes(DATA)
    original = fake.complete_multipart_upload

    def slow_complete(**kwargs):
        if kwargs["UploadId"] in fake.uploads:
            original(**kwargs)  # the server finishes, the client times out
            raise ReadTimeoutError(endpoint_url="https://s3")
        raise _client_error("NoSuchUpload", 404)

    fake.complete_multipart_upload = slow_complete
    summary = _transfer(fake, tmp_path).upload(src, "big.bin")
    assert fake.objects["big.bin"][0] == DATA
    assert summary["files_transferred"] == 1
    assert not list((tmp_path / "state").glob("*.json"))


def test_complete_failure_without_object_keeps_state(tmp_path):
    fake = FakeS3()
    src = tmp_path / "big.bin"
    src.write_bytes(DATA)
    fake.fail = lambda op, kw: (
        _client_error("AccessDenied", 403) if op == "complete_multipart_upload" else None
    )
    with pytest.raises(ClientError):
        _transfer(fake, tmp_path).upload(src, "big.bin")
    assert len(list((tmp_path / "state").glob("*.json"))) == 1


def test_transient_errors_are_retried(tmp_path):
    fake = FakeS3()
    src = tmp_path / "big.bin"
    src.write_bytes(DATA)
    failed = set()

    def flaky(op, kw):
        if op == "upload_part" and kw["PartNumber"] not in failed:
            failed.add(kw["PartNumber"])
            return _client_error("524", 524)
        return None

    fake.fail = flaky
    _transfer(fake, tmp_path).upload(src, "big.bin")
    assert len(fake.ops("upload_part")) == 10
    assert fake.objects["big.bin"][0] == DATA


def test_retry_gives_up_after_attempts():
    calls = []

    def always_503():
        calls.append(1)
        raise _client_error("ServiceUnavailable", 503)

    with pytest.raises(ClientError):
        nvt._with_retries(always_503, attempts=3)
    assert len(calls) == 3


def test_folder_upload_skips_unchanged(tmp_path):
    fake = FakeS3()
    root = tmp_path / "data"
    (root / "b").mkdir(parents=True)
    (root / "a.txt").write_bytes(b"aaa")
    (root / "b" / "c.txt").write_bytes(b"cccc")
    (root / "d.txt").write_bytes(b"dd")
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    fake.put("models/a.txt", b"xyz", future)  # same size, newer: skip
    fake.put("models/d.txt", b"zz", past)  # same size, older: upload

    summary = _transfer(fake, tmp_path, concurrency=2).upload(root, "models")
    assert sorted(kw["Key"] for kw in fake.ops("put_object")) == ["models/b/c.txt", "models/d.txt"]
    assert fake.objects["models/a.txt"][0] == b"xyz"
    assert summary["files_skipped"] == 1 and summary["files_transferred"] == 2


def test_folder_upload_default_prefix_and_failure_report(tmp_path):
    fake = FakeS3()
    root = tmp_path / "data"
    root.mkdir()
    (root / "ok.txt").write_bytes(b"1")
    (root / "bad.txt").write_bytes(b"2")
    fake.fail = lambda op, kw: (
        _client_error("AccessDenied", 403) if kw.get("Key") == "data/bad.txt" else None
    )
    with pytest.raises(nvt.NetworkVolumeTransferError, match="1 file"):
        _transfer(fake, tmp_path).upload(root, None)
    assert "data/ok.txt" in fake.objects


def test_single_file_upload_skips_unchanged(tmp_path):
    fake = FakeS3()
    src = tmp_path / "f.txt"
    src.write_bytes(b"abc")
    fake.put("f.txt", b"abc", datetime.now(timezone.utc) + timedelta(hours=1))
    summary = _transfer(fake, tmp_path).upload(src, None)
    assert summary["files_skipped"] == 1
    assert not fake.ops("put_object")


def test_upload_missing_local_path(tmp_path):
    with pytest.raises(FileNotFoundError):
        _transfer(FakeS3(), tmp_path).upload(tmp_path / "nope", None)


# -------------------------------------------------------------- download


def test_download_resumes_from_sidecar(tmp_path):
    fake = FakeS3()
    fake.put("big.bin", DATA)
    dest = tmp_path / "out" / "big.bin"
    fake.fail = lambda op, kw: (
        _client_error("AccessDenied", 403)
        if op == "get_object" and kw["Range"] == "bytes=32-39"
        else None
    )
    with pytest.raises(ClientError):
        _transfer(fake, tmp_path).download("big.bin", dest)
    sidecar = json.loads((tmp_path / "out" / "big.bin.rpdownload.json").read_text())
    assert sidecar["done"] == [0, 1, 2, 3]
    assert not dest.exists()

    fake.fail = None
    fake.calls.clear()
    summary = _transfer(fake, tmp_path).download("big.bin", dest)
    assert [kw["Range"] for kw in fake.ops("get_object")] == ["bytes=32-39"]
    assert dest.read_bytes() == DATA
    assert not (tmp_path / "out" / "big.bin.rpdownload.json").exists()
    assert not (tmp_path / "out" / "big.bin.rpdownload").exists()
    assert summary["bytes_transferred"] == len(DATA)


def test_download_restarts_when_remote_changed(tmp_path):
    fake = FakeS3()
    fake.put("big.bin", DATA)
    dest = tmp_path / "big.bin"
    fake.fail = lambda op, kw: (
        _client_error("AccessDenied", 403) if kw.get("Range") == "bytes=16-23" else None
    )
    with pytest.raises(ClientError):
        _transfer(fake, tmp_path).download("big.bin", dest)
    fake.fail = None
    fake.put("big.bin", DATA[::-1])
    fake.calls.clear()
    _transfer(fake, tmp_path).download("big.bin", dest)
    assert len(fake.ops("get_object")) == 5
    assert dest.read_bytes() == DATA[::-1]


def test_download_stops_when_remote_replaced_midway(tmp_path):
    fake = FakeS3()
    fake.put("big.bin", DATA)
    dest = tmp_path / "big.bin"
    original = fake.get_object

    def replace_after_first(**kwargs):
        response = original(**kwargs)
        fake.put("big.bin", DATA[::-1])
        return response

    fake.get_object = replace_after_first
    with pytest.raises(nvt.NetworkVolumeTransferError, match="changed on the volume") as info:
        _transfer(fake, tmp_path).download("big.bin", dest)
    assert info.value.resumable
    assert not dest.exists()


def test_downloaded_folder_uploads_back_as_unchanged(tmp_path):
    fake = FakeS3()
    old = datetime(2024, 1, 1, tzinfo=timezone.utc)
    fake.put("data/a.bin", DATA, modified=old)
    fake.put("data/sub/b.txt", b"b", modified=old)
    local = tmp_path / "data"
    _transfer(fake, tmp_path).download("data", local)
    assert (local / "a.bin").stat().st_mtime == old.timestamp()
    fake.calls.clear()
    summary = _transfer(fake, tmp_path).upload(local, "data")
    assert summary["files_skipped"] == 2
    assert not fake.ops("put_object") and not fake.ops("upload_part")


def test_download_short_read_is_retried(tmp_path):
    fake = FakeS3()
    fake.put("f.bin", DATA)
    original = fake.get_object
    shorted = []

    def short_once(**kwargs):
        response = original(**kwargs)
        if not shorted:
            shorted.append(1)
            return {"Body": io.BytesIO(response["Body"].read()[:-1])}
        return response

    fake.get_object = short_once
    _transfer(fake, tmp_path).download("f.bin", tmp_path / "f.bin")
    assert (tmp_path / "f.bin").read_bytes() == DATA


def test_download_file_into_existing_dir_and_skip(tmp_path):
    fake = FakeS3()
    fake.put("models/w.bin", b"weights")
    fake.put("empty.bin", b"")
    _transfer(fake, tmp_path).download("models/w.bin", tmp_path)
    assert (tmp_path / "w.bin").read_bytes() == b"weights"
    summary = _transfer(fake, tmp_path).download("/models/w.bin", tmp_path)
    assert summary["files_skipped"] == 1
    _transfer(fake, tmp_path).download("empty.bin", tmp_path / "e.bin")
    assert (tmp_path / "e.bin").read_bytes() == b""


def test_folder_download_filters_and_defaults(tmp_path, monkeypatch):
    fake = FakeS3()
    fake.put("models/a.bin", b"a")
    fake.put("models/sub/", b"")
    fake.put("models/sub/b.bin", b"bb")
    fake.put("models/.s3compat_uploads/x", b"junk")
    fake.put("other/c.bin", b"c")
    monkeypatch.chdir(tmp_path)
    summary = _transfer(fake, tmp_path, concurrency=2).download("models", None)
    assert (tmp_path / "models" / "a.bin").read_bytes() == b"a"
    assert (tmp_path / "models" / "sub" / "b.bin").read_bytes() == b"bb"
    assert not (tmp_path / "models" / ".s3compat_uploads").exists()
    assert summary["files_transferred"] == 2


def test_folder_download_when_head_on_directory_is_400(tmp_path):
    fake = FakeS3()
    fake.put("models/a.bin", b"a")
    original = fake.head_object

    def head(**kwargs):
        if kwargs["Key"] == "models":
            raise _client_error("BadRequest", 400, "HeadObject")
        return original(**kwargs)

    fake.head_object = head
    summary = _transfer(fake, tmp_path).download("models", tmp_path / "dl")
    assert (tmp_path / "dl" / "a.bin").read_bytes() == b"a"
    assert summary["files_transferred"] == 1


def test_head_400_outside_download_is_an_error(tmp_path):
    fake = FakeS3()
    fake.fail = lambda op, kw: _client_error("BadRequest", 400) if op == "head_object" else None
    src = tmp_path / "f.txt"
    src.write_bytes(b"x")
    with pytest.raises(ClientError):
        _transfer(fake, tmp_path).upload(src, "f.txt")


@pytest.mark.parametrize(
    "exc, expected",
    [
        (FileNotFoundError("x"), False),
        (_client_error("AccessDenied", 403), False),
        (_client_error("InvalidAccessKeyId", 401), False),
        (_client_error("NoSuchKey", 404), False),
        (nvt.NetworkVolumeTransferError("keys required"), False),
        (nvt.NetworkVolumeTransferError("cut", resumable=True), True),
        (_client_error("524", 524), True),
        (ReadTimeoutError(endpoint_url="https://s3"), True),
    ],
)
def test_is_resumable(exc, expected):
    assert nvt.is_resumable(exc) is expected


def test_is_resumable_runpod_api_errors():
    assert not nvt.is_resumable(runpod.error.AuthenticationError("bad key"))


def test_folder_failure_resumable_only_if_a_file_was(tmp_path):
    fake = FakeS3()
    root = tmp_path / "data"
    root.mkdir()
    (root / "a.txt").write_bytes(b"1")
    fake.fail = lambda op, kw: _client_error("AccessDenied", 403) if op == "put_object" else None
    with pytest.raises(nvt.NetworkVolumeTransferError) as denied:
        _transfer(fake, tmp_path).upload(root, None)
    assert denied.value.resumable is False
    fake.fail = lambda op, kw: ConnectionResetError("reset") if op == "put_object" else None
    with pytest.raises(nvt.NetworkVolumeTransferError) as reset:
        _transfer(fake, tmp_path).upload(root, None)
    assert reset.value.resumable is True


def test_folder_download_rejects_path_traversal(tmp_path):
    fake = FakeS3()
    fake.put("models/ok.bin", b"a")
    fake.put("models/../evil.bin", b"x")
    with pytest.raises(nvt.NetworkVolumeTransferError, match="unsafe"):
        _transfer(fake, tmp_path).download("models/", tmp_path / "dl")
    assert not (tmp_path / "evil.bin").exists()
    assert not fake.ops("get_object")


@pytest.mark.parametrize("rel", ["/abs", "C:/x", "a\\..\\b", ""])
def test_safe_target_rejects(tmp_path, rel):
    with pytest.raises(nvt.NetworkVolumeTransferError):
        nvt._safe_target(tmp_path, rel)


def test_download_missing_remote(tmp_path):
    with pytest.raises(FileNotFoundError):
        _transfer(FakeS3(), tmp_path).download("nope", tmp_path / "x")


# ---------------------------------------------------------------- client


def test_make_transfer_requires_s3_keys(monkeypatch):
    monkeypatch.delenv(nvt.ACCESS_KEY_ENV, raising=False)
    monkeypatch.delenv(nvt.SECRET_KEY_ENV, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "aws")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws")
    with pytest.raises(nvt.NetworkVolumeTransferError, match="RUNPOD_S3_ACCESS_KEY"):
        nvt.upload("vol", ".", datacenter="EU-RO-1")


def test_make_transfer_builds_datacenter_client(monkeypatch):
    monkeypatch.setenv(nvt.ACCESS_KEY_ENV, "user_x")
    monkeypatch.setenv(nvt.SECRET_KEY_ENV, "rps_x")
    with patch(
        "runpod.api.rest.run_rest_request", return_value={"dataCenter": "EU-RO-1"}
    ) as rest:
        transfer = nvt._make_transfer("vol123", None, None, None, None, 4, None)
    rest.assert_called_once_with("GET", "/v2/network-volumes/vol123")
    assert transfer.client.meta.endpoint_url == "https://s3api-eu-ro-1.runpod.io/"
    assert transfer.client.meta.region_name == "EU-RO-1"
    assert transfer.client.meta.config.request_checksum_calculation == "when_required"
    credentials = transfer.client._request_signer._credentials
    assert credentials.access_key == "user_x"
    assert transfer.bucket == "vol123"


def test_lookup_datacenter_missing():
    with patch("runpod.api.rest.run_rest_request", return_value={}):
        with pytest.raises(nvt.NetworkVolumeTransferError):
            nvt._lookup_datacenter("vol")


def test_public_functions_delegate(tmp_path):
    fake_transfer = type(
        "T", (), {"upload": lambda self, l, r: ("up", l, r), "download": lambda self, r, l: ("down", r, l)}
    )()
    with patch.object(nvt, "_make_transfer", return_value=fake_transfer):
        assert nvt.upload("vol", "x", "y")[0] == "up"
        assert nvt.download("vol", "y", "x")[0] == "down"


def test_lazy_exports():
    assert runpod.upload_to_network_volume is nvt.upload
    assert runpod.download_from_network_volume is nvt.download


# ------------------------------------------------------------------- cli


def test_cli_upload_prints_summary(tmp_path):
    from runpod.rp_cli.main import cli

    src = tmp_path / "f.txt"
    src.write_text("x")

    def fake_upload(volume_id, source, target, **kwargs):
        progress = kwargs["progress"]
        progress("start", source, 1)
        progress("advance", source, 1)
        progress("done", source, 0)
        assert kwargs["datacenter"] == "EU-RO-1" and kwargs["concurrency"] == 2
        return {"files_transferred": 1, "files_skipped": 0, "bytes_transferred": 1}

    with patch.object(nvt, "upload", side_effect=fake_upload):
        result = CliRunner().invoke(
            cli,
            ["network-volume", "upload", "vol", str(src), "--datacenter", "EU-RO-1", "--concurrency", "2"],
        )
    assert result.exit_code == 0, result.output
    assert "uploaded 1 file(s), 1 bytes; skipped 0 unchanged" in result.output


def test_cli_download_failure_says_resume():
    from runpod.rp_cli.main import cli

    with patch.object(nvt, "download", side_effect=ConnectionResetError("boom")):
        result = CliRunner().invoke(cli, ["network-volume", "download", "vol", "a/b"])
    assert result.exit_code != 0
    assert "boom" in result.output
    assert "re-run the same command to resume" in result.output


def test_cli_missing_path_has_no_resume_hint():
    from runpod.rp_cli.main import cli

    with patch.object(nvt, "download", side_effect=FileNotFoundError("no such file")):
        result = CliRunner().invoke(cli, ["network-volume", "download", "vol", "a/b"])
    assert result.exit_code != 0
    assert "no such file" in result.output
    assert "resume" not in result.output


# ------------------------------------------------- review follow-ups


@pytest.mark.parametrize(
    "error",
    [_client_error("InternalError", 524), _client_error("InvalidPart", 400)],
    ids=["ambiguous-524-old-object", "definite-failure"],
)
def test_failed_complete_does_not_accept_an_old_object(tmp_path, error):
    fake = FakeS3()
    src = tmp_path / "big.bin"
    src.write_bytes(DATA)
    # an older object of the same size already sits at the key
    fake.put("big.bin", b"o" * len(DATA), modified=datetime(2020, 1, 1, tzinfo=timezone.utc))
    fake.fail = lambda op, kw: error if op == "complete_multipart_upload" else None
    with pytest.raises(ClientError):
        _transfer(fake, tmp_path).upload(src, "big.bin")
    assert len(list((tmp_path / "state").glob("*.json"))) == 1


def test_local_file_changed_during_upload_aborts(tmp_path):
    fake = FakeS3()
    src = tmp_path / "big.bin"
    src.write_bytes(DATA)

    def rewrite(op, kw):
        if op == "upload_part" and kw["PartNumber"] == 2:
            src.write_bytes(b"n" * len(DATA))
            later = src.stat().st_mtime + 60
            os.utime(src, (later, later))
        return None

    fake.fail = rewrite
    with pytest.raises(nvt.NetworkVolumeTransferError, match="changed during upload"):
        _transfer(fake, tmp_path).upload(src, "big.bin")
    assert len(fake.ops("abort_multipart_upload")) == 1
    assert not fake.ops("complete_multipart_upload")
    assert not list((tmp_path / "state").glob("*.json"))


def test_unsavable_state_aborts_the_upload(tmp_path, monkeypatch):
    fake = FakeS3()
    src = tmp_path / "big.bin"
    src.write_bytes(DATA)

    def no_disk(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(nvt, "_write_json_atomic", no_disk)
    with pytest.raises(OSError):
        _transfer(fake, tmp_path).upload(src, "big.bin")
    assert len(fake.ops("abort_multipart_upload")) == 1


def test_download_refetches_newer_same_size_remote(tmp_path):
    fake = FakeS3()
    fake.put("f.txt", b"version-1")
    dest = tmp_path / "f.txt"
    _transfer(fake, tmp_path).download("f.txt", dest)
    fake.put("f.txt", b"version-2", modified=datetime.now(timezone.utc) + timedelta(minutes=1))
    summary = _transfer(fake, tmp_path).download("f.txt", dest)
    assert summary["files_transferred"] == 1
    assert dest.read_bytes() == b"version-2"


def test_download_ignores_sidecar_of_another_key(tmp_path):
    fake = FakeS3()
    fake.put("b.bin", DATA)
    dest = tmp_path / "x.bin"
    (tmp_path / "x.bin.rpdownload").write_bytes(b"z" * len(DATA))
    (tmp_path / "x.bin.rpdownload.json").write_text(
        json.dumps(
            {
                "key": "a.bin",
                "size": len(DATA),
                "etag": nvt._strip_etag(fake.object_etag("b.bin")),
                "chunk_size": nvt.DOWNLOAD_CHUNK_SIZE,
                "done": [0, 1, 2, 3, 4],
            }
        )
    )
    _transfer(fake, tmp_path).download("b.bin", dest)
    assert dest.read_bytes() == DATA


def test_download_refuses_partial_name_collision(tmp_path):
    fake = FakeS3()
    fake.put("d/x", b"x")
    fake.put("d/x.rpdownload", b"y")
    with pytest.raises(nvt.NetworkVolumeTransferError, match="partial download"):
        _transfer(fake, tmp_path).download("d/", tmp_path / "out")


@pytest.mark.parametrize("op", ["upload", "download"])
def test_dotdot_remote_path_rejected(tmp_path, op):
    fake = FakeS3()
    src = tmp_path / "f.txt"
    src.write_bytes(b"x")
    transfer = _transfer(fake, tmp_path)
    with pytest.raises(ValueError, match=r"\.\."):
        if op == "upload":
            transfer.upload(src, "a/../b")
        else:
            transfer.download("a/../b", tmp_path / "out")
    assert not fake.calls
    assert not nvt.is_resumable(ValueError("x"))


def test_requests_in_flight_never_exceed_concurrency(tmp_path):
    import threading
    import time as _time

    fake = FakeS3()
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()
    original = fake.upload_part

    def tracked(**kwargs):
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        _time.sleep(0.01)
        try:
            return original(**kwargs)
        finally:
            with lock:
                active["now"] -= 1

    fake.upload_part = tracked
    folder = tmp_path / "data"
    folder.mkdir()
    for i in range(4):
        (folder / f"f{i}.bin").write_bytes(DATA)
    _transfer(fake, tmp_path, concurrency=2).upload(folder, "data")
    assert active["peak"] <= 2
