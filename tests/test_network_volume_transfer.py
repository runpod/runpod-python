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

# the real helpers, before the autouse fixture stubs them
REAL_WAIT = nvt._wait
REAL_ENDPOINT_CHECK = nvt._check_endpoint_resolves


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

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None, MaxKeys=None):
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

    def delete_object(self, Bucket, Key):
        self._call("delete_object", Key=Key)
        self.objects.pop(Key, None)
        self.multipart_etags.pop(Key, None)
        return {}

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self._call("abort_multipart_upload", UploadId=UploadId)
        self.uploads.pop(UploadId, None)

    def ops(self, op):
        return [kwargs for name, kwargs in self.calls if name == op]


@pytest.fixture(autouse=True)
def small_sizes(monkeypatch):
    monkeypatch.setattr(nvt, "_sleep", lambda _: None)
    monkeypatch.setattr(nvt, "_wait", lambda delay, stop=None: None)
    # transfers built through _make_transfer must not hit dns in tests
    monkeypatch.setattr(nvt, "_check_endpoint_resolves", lambda endpoint: None)
    monkeypatch.setattr(nvt, "SINGLE_PUT_MAX", 10)
    monkeypatch.setattr(nvt, "DEFAULT_PART_SIZE", 8)
    monkeypatch.setattr(nvt, "DOWNLOAD_CHUNK_SIZE", 8)


def _transfer(fake, tmp_path, concurrency=1, events=None):
    progress = None if events is None else (lambda e, p, n: events.append((e, p, n)))
    return nvt._Transfer(
        fake, "vol", "https://s3", concurrency, progress, state_dir=tmp_path / "state"
    )


_VOLUME = {"id": "vol123", "name": "models", "dataCenterId": "EU-RO-1"}


def _list_volumes(*volumes):
    """patch the paginated volume listing to return these records."""
    calls = []

    async def fake(path, collection, **kwargs):
        calls.append((path, collection))
        return list(volumes)

    return patch("runpod.api.rest.run_rest_paginated_request_async", fake), calls


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
    # only the resent parts (2 and 5) count as transferred
    assert summary["bytes_transferred"] == 16
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


def test_folder_failure_is_resumable_only_when_every_failure_is(tmp_path):
    fake = FakeS3()
    root = tmp_path / "data"
    root.mkdir()
    (root / "cut.txt").write_bytes(b"1")
    (root / "denied.txt").write_bytes(b"2")
    errors = {
        "data/cut.txt": _client_error("ServiceUnavailable", 503),
        "data/denied.txt": _client_error("AccessDenied", 403),
    }
    fake.fail = lambda op, kw: errors.get(kw.get("Key")) if op == "put_object" else None
    with pytest.raises(nvt.NetworkVolumeTransferError, match="2 file") as mixed:
        _transfer(fake, tmp_path).upload(root, None)
    assert not nvt.is_resumable(mixed.value)
    del errors["data/denied.txt"]
    with pytest.raises(nvt.NetworkVolumeTransferError, match="1 file") as cut:
        _transfer(fake, tmp_path).upload(root, None)
    assert nvt.is_resumable(cut.value)


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
    # only the missing chunk counts as transferred
    assert summary["bytes_transferred"] == 8


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
    fake.put(f"models/{nvt.UPLOADS_DIR}/x", b"junk")
    fake.put("other/c.bin", b"c")
    monkeypatch.chdir(tmp_path)
    summary = _transfer(fake, tmp_path, concurrency=2).download("models", None)
    assert (tmp_path / "models" / "a.bin").read_bytes() == b"a"
    assert (tmp_path / "models" / "sub" / "b.bin").read_bytes() == b"bb"
    assert not (tmp_path / "models" / nvt.UPLOADS_DIR).exists()
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


def test_upload_onto_remote_folder_is_refused(tmp_path):
    fake = FakeS3()
    fake.put("models/a.bin", b"a")
    # runpod answers a head on a directory with 400
    fake.fail = lambda op, kw: _client_error("BadRequest", 400) if op == "head_object" else None
    src = tmp_path / "f.txt"
    src.write_bytes(b"x")
    with pytest.raises(nvt.NetworkVolumeTransferError, match="'models/'") as info:
        _transfer(fake, tmp_path).upload(src, "models")
    assert not nvt.is_resumable(info.value)


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
        (KeyboardInterrupt(), True),
        # unknown or local failures repeat identically on a re-run
        (RuntimeError("param validation"), False),
        (OSError(28, "No space left on device"), False),
        (ValueError("x"), False),
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
        nvt._make_transfer("vol", None, None, None, 4, None)


def test_make_transfer_builds_datacenter_client(monkeypatch):
    # a stale local aws profile must not leak into the session
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(nvt.ACCESS_KEY_ENV, "user_x")
    monkeypatch.setenv(nvt.SECRET_KEY_ENV, "rps_x")
    listing, calls = _list_volumes(_VOLUME)
    with listing:
        transfer = nvt._make_transfer("models", None, None, None, 4, None)
    assert calls == [("/v2/network-volumes", "networkVolumes")]
    assert transfer.client.meta.endpoint_url == "https://s3api-eu-ro-1.runpod.io/"
    assert transfer.client.meta.region_name == "EU-RO-1"
    assert transfer.client.meta.config.request_checksum_calculation == "when_required"
    # _with_retries owns retries
    assert transfer.client.meta.config.retries["total_max_attempts"] == 1
    credentials = transfer.client._request_signer._credentials
    assert credentials.access_key == "user_x"
    assert transfer.bucket == "vol123"


def test_resolve_volume_by_name():
    listing, _ = _list_volumes({"id": "other", "name": "x", "dataCenterId": "US-KS-2"}, _VOLUME)
    with listing:
        assert nvt._resolve_volume("models") == ("vol123", "EU-RO-1")


def test_resolve_volume_by_id():
    listing, _ = _list_volumes(_VOLUME)
    with listing:
        assert nvt._resolve_volume("vol123") == ("vol123", "EU-RO-1")


@pytest.mark.parametrize(
    "volumes, match",
    [
        ([], "not found"),
        ([{**_VOLUME, "id": "a"}, {**_VOLUME, "id": "b"}], "reference by id"),
        ([{"id": "vol123", "name": "models"}], "datacenter"),
    ],
    ids=["missing", "ambiguous-name", "no-datacenter"],
)
def test_resolve_volume_errors(volumes, match):
    listing, _ = _list_volumes(*volumes)
    with listing:
        with pytest.raises(nvt.NetworkVolumeTransferError, match=match):
            nvt._resolve_volume("models")


def test_network_volume_methods_delegate():
    calls = []
    fake_transfer = type(
        "T", (), {"upload": lambda self, l, r: ("up", l, r), "download": lambda self, r, l: ("down", r, l)}
    )()

    def make(*args, **kwargs):
        calls.append(args)
        return fake_transfer

    volume = runpod.NetworkVolume("models", create=False)
    with patch.object(nvt, "_make_transfer", side_effect=make):
        assert volume.upload("x", "y", concurrency=2)[0] == "up"
        assert volume.download("y", "x")[0] == "down"
    assert calls[0][0] == "models" and calls[0][4] == 2


def test_network_volume_download_keeps_trailing_slash(tmp_path, monkeypatch):
    fake = FakeS3()
    fake.put("w.bin", b"w")
    monkeypatch.setattr(nvt, "_make_transfer", lambda *a, **k: _transfer(fake, tmp_path))
    runpod.NetworkVolume("vol").download("w.bin", str(tmp_path / "out") + "/")
    assert (tmp_path / "out" / "w.bin").read_bytes() == b"w"


def test_no_module_level_transfer_exports():
    for name in ("upload_to_network_volume", "download_from_network_volume", "network_volume_transfer"):
        assert name not in runpod.__all__


# ------------------------------------------------------------------- cli


def test_cli_upload_prints_summary(tmp_path):
    from runpod.rp_cli.main import cli

    src = tmp_path / "f.txt"
    src.write_text("x")

    def fake_upload(volume, source, target, **kwargs):
        volume = volume.reference
        progress = kwargs["progress"]
        progress("start", source, 1)
        progress("advance", source, 1)
        progress("done", source, 0)
        assert volume == "models" and kwargs["concurrency"] == 2
        return {"files_transferred": 1, "files_skipped": 0, "bytes_transferred": 1}

    with patch.object(runpod.NetworkVolume, "upload", autospec=True, side_effect=fake_upload):
        result = CliRunner().invoke(
            cli,
            ["volume", "upload", "models", str(src), "--concurrency", "2"],
        )
    assert result.exit_code == 0, result.output
    assert "uploaded 1 file(s), 1 bytes; skipped 0 unchanged" in result.output


def test_cli_download_failure_says_resume():
    from runpod.rp_cli.main import cli

    with patch.object(runpod.NetworkVolume, "download", side_effect=ConnectionResetError("boom")):
        result = CliRunner().invoke(cli, ["volume", "download", "vol", "a/b"])
    assert result.exit_code != 0
    assert "boom" in result.output
    assert "re-run the same command to resume" in result.output


def test_cli_ctrl_c_exits_130_with_resume_hint():
    from runpod.rp_cli.main import cli

    with patch.object(runpod.NetworkVolume, "upload", side_effect=KeyboardInterrupt):
        result = CliRunner().invoke(cli, ["volume", "upload", "vol", "."])
    assert result.exit_code == 130
    assert "interrupted; re-run the same command to resume" in result.output


def test_cli_unresolvable_endpoint_has_no_resume_hint():
    from runpod.rp_cli.main import cli

    err = nvt.NetworkVolumeTransferError("the s3 endpoint s3api-x.invalid does not resolve")
    with patch.object(runpod.NetworkVolume, "download", side_effect=err):
        result = CliRunner().invoke(cli, ["volume", "download", "vol", "a/b"])
    assert result.exit_code == 1
    assert "does not resolve" in result.output
    assert "re-run" not in result.output


def test_cli_missing_path_has_no_resume_hint():
    from runpod.rp_cli.main import cli

    with patch.object(runpod.NetworkVolume, "download", side_effect=FileNotFoundError("no such file")):
        result = CliRunner().invoke(cli, ["volume", "download", "vol", "a/b"])
    assert result.exit_code != 0
    assert "no such file" in result.output
    assert "resume" not in result.output


# ------------------------------------------- completion, collisions, limits


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


@pytest.mark.parametrize(
    "other",
    [
        "d/x.rpdownload",
        "d/x.rpdownload.json",
        "d/x.rpdownload.json.tmp",
        "d/x.rpdownload/y",
        "d/X.RPDOWNLOAD",
    ],
)
def test_download_refuses_partial_name_collision(tmp_path, other):
    fake = FakeS3()
    fake.put("d/x", b"x")
    fake.put(other, b"y")
    with pytest.raises(nvt.NetworkVolumeTransferError, match="partial download"):
        _transfer(fake, tmp_path).download("d/", tmp_path / "out")
    assert not fake.ops("get_object")


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


def test_ambiguous_complete_without_verifiable_etag_fails(tmp_path):
    fake = FakeS3(md5_etags=False)
    src = tmp_path / "big.bin"
    src.write_bytes(DATA)
    fake.put("big.bin", b"o" * len(DATA), modified=datetime(2020, 1, 1, tzinfo=timezone.utc))
    fake.fail = lambda op, kw: (
        _client_error("InternalError", 524) if op == "complete_multipart_upload" else None
    )
    with pytest.raises(ClientError):
        _transfer(fake, tmp_path).upload(src, "big.bin")


def test_single_put_detects_change_between_retries(tmp_path):
    fake = FakeS3()
    src = tmp_path / "small.txt"
    src.write_bytes(b"original")
    attempts = []

    def change_then_fail(op, kw):
        if op == "put_object" and not attempts:
            attempts.append(1)
            src.write_bytes(b"CHANGED!")
            later = src.stat().st_mtime + 60
            os.utime(src, (later, later))
            return _client_error("ServiceUnavailable", 503)
        return None

    fake.fail = change_then_fail
    with pytest.raises(nvt.NetworkVolumeTransferError, match="changed during upload"):
        _transfer(fake, tmp_path).upload(src, "small.txt")


def test_all_requests_share_the_concurrency_limit(tmp_path):
    import threading
    import time as _time

    fake = FakeS3()
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()
    original_call = fake._call

    def tracked_call(op, **kwargs):
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        _time.sleep(0.005)
        with lock:
            active["now"] -= 1
        return original_call(op, **kwargs)

    fake._call = tracked_call
    folder = tmp_path / "data"
    folder.mkdir()
    for i in range(8):
        (folder / f"f{i}.bin").write_bytes(DATA + bytes([i]))
    _transfer(fake, tmp_path, concurrency=2).upload(folder, "data")
    _transfer(fake, tmp_path, concurrency=2).download("data/", tmp_path / "out")
    assert active["peak"] <= 2


@pytest.fixture
def endpoint_check(monkeypatch):
    """the real endpoint check with dns and proxies stubbed (the autouse
    fixture replaces the check itself)."""
    import socket as _socket
    import urllib.request

    monkeypatch.setattr(nvt, "_check_endpoint_resolves", REAL_ENDPOINT_CHECK)
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    # proxies from the environment only: macos and windows also read system settings
    monkeypatch.setattr(urllib.request, "getproxies", urllib.request.getproxies_environment)
    monkeypatch.setattr(urllib.request, "proxy_bypass", urllib.request.proxy_bypass_environment)
    state = {"error": None}

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if state["error"] is not None:
            raise state["error"]
        return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(_socket, "getaddrinfo", fake_getaddrinfo)
    return state


def test_unresolvable_endpoint_fails_before_the_transfer(endpoint_check):
    import socket as _socket

    endpoint_check["error"] = _socket.gaierror(8, "nodename nor servname provided")
    with pytest.raises(nvt.NetworkVolumeTransferError, match="does not resolve"):
        nvt._check_endpoint_resolves("https://s3api-nope-1.runpod.io/")
    endpoint_check["error"] = None
    nvt._check_endpoint_resolves("https://s3api-eu-ro-1.runpod.io/")


def test_endpoint_check_skipped_behind_a_proxy(endpoint_check, monkeypatch):
    import socket as _socket

    endpoint_check["error"] = _socket.gaierror(8, "nodename nor servname provided")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    nvt._check_endpoint_resolves("https://s3api-eu-ro-1.runpod.io/")


def test_explicit_endpoint_skips_the_dns_check(endpoint_check, monkeypatch):
    import socket as _socket

    monkeypatch.setenv(nvt.ACCESS_KEY_ENV, "user_x")
    monkeypatch.setenv(nvt.SECRET_KEY_ENV, "rps_x")
    monkeypatch.setattr(nvt, "_resolve_volume", lambda volume: ("vol123", "NOPE-1"))
    endpoint_check["error"] = _socket.gaierror(8, "nodename nor servname provided")
    transfer = nvt._make_transfer("vol123", None, None, "http://localhost:9000", 4, None)
    assert transfer.client.meta.endpoint_url == "http://localhost:9000"
    with pytest.raises(nvt.NetworkVolumeTransferError, match="does not resolve"):
        nvt._make_transfer("vol123", None, None, None, 4, None)


def test_dns_failure_mid_transfer_is_a_network_drop():
    import socket as _socket

    from botocore.exceptions import EndpointConnectionError

    exc = EndpointConnectionError(
        endpoint_url="https://s3api-eu-ro-1.runpod.io/", error=_socket.gaierror(8, "nodename nor servname")
    )
    assert nvt._is_retryable(exc)
    assert nvt.is_resumable(exc)


def test_head_400_that_is_not_a_folder_is_reported_as_is(tmp_path):
    fake = FakeS3()
    fake.fail = lambda op, kw: _client_error("BadRequest", 400) if op == "head_object" else None
    src = tmp_path / "f.txt"
    src.write_bytes(b"x")
    with pytest.raises(ClientError):
        _transfer(fake, tmp_path).upload(src, "f.txt")


def test_vanished_local_file_keeps_the_uploaded_object(tmp_path):
    fake = FakeS3()
    src = tmp_path / "small.txt"
    src.write_bytes(b"original")
    original = fake.put_object

    def put_then_vanish(**kwargs):
        result = original(**kwargs)
        src.unlink()
        return result

    fake.put_object = put_then_vanish
    with pytest.raises(nvt.NetworkVolumeTransferError, match="gone"):
        _transfer(fake, tmp_path).upload(src, "small.txt")
    assert fake.objects["small.txt"][0] == b"original"
    assert not fake.ops("delete_object")


def test_failed_delete_after_change_asks_for_force(tmp_path):
    fake = FakeS3()
    src = tmp_path / "small.txt"
    src.write_bytes(b"original")
    original = fake.put_object

    def put_then_edit(**kwargs):
        result = original(**kwargs)
        src.write_bytes(b"CHANGED!")
        later = src.stat().st_mtime + 60
        os.utime(src, (later, later))
        return result

    fake.put_object = put_then_edit
    fake.fail = lambda op, kw: _client_error("AccessDenied", 403) if op == "delete_object" else None
    with pytest.raises(nvt.NetworkVolumeTransferError, match="--force") as info:
        _transfer(fake, tmp_path).upload(src, "small.txt")
    assert not nvt.is_resumable(info.value)


@pytest.mark.parametrize(
    "exc", [_client_error("NoSuchUpload", 404), _client_error("InvalidRange", 416)]
)
def test_vanished_upload_and_stale_range_resume(exc):
    assert nvt.is_resumable(exc)


def test_download_refuses_file_that_is_also_a_folder(tmp_path):
    fake = FakeS3()
    fake.put("d/a", b"file")
    fake.put("d/A/b", b"nested")
    with pytest.raises(nvt.NetworkVolumeTransferError, match="file and a folder"):
        _transfer(fake, tmp_path).download("d/", tmp_path / "out")


def test_retry_backoff_ends_when_stopped(monkeypatch):
    import threading
    import time as _time

    monkeypatch.setattr(nvt, "_wait", REAL_WAIT)
    # the real sleep too, so a revert to an uninterruptible sleep is caught
    monkeypatch.setattr(nvt, "_sleep", _time.sleep)
    stop = threading.Event()
    calls = []

    def fail():
        calls.append(1)
        if len(calls) == 1:
            stop.set()  # ctrl-c arrives during the first attempt
        raise ConnectionError("down")

    started = _time.monotonic()
    with pytest.raises(ConnectionError):
        nvt._with_retries(fail, attempts=3, stop=stop)
    # the backoffs (1s, 2s plus jitter) are cut short
    assert _time.monotonic() - started < 0.5


def test_single_put_change_with_older_mtime_is_resent_on_rerun(tmp_path):
    fake = FakeS3()
    src = tmp_path / "small.txt"
    src.write_bytes(b"original")
    old = src.stat().st_mtime - 3600
    os.utime(src, (old, old))
    attempts = []

    def edit_then_fail(op, kw):
        if op == "put_object" and not attempts:
            attempts.append(1)
            # a same-size edit whose mtime is older than the object the retry
            # writes, so the skip rule alone would keep the object
            src.write_bytes(b"CHANGED!")
            past = src.stat().st_mtime - 1800
            os.utime(src, (past, past))
            return _client_error("ServiceUnavailable", 503)
        return None

    fake.fail = edit_then_fail
    with pytest.raises(nvt.NetworkVolumeTransferError, match="changed during upload"):
        _transfer(fake, tmp_path).upload(src, "small.txt")
    assert "small.txt" not in fake.objects
    fake.fail = None
    _transfer(fake, tmp_path).upload(src, "small.txt")
    assert fake.objects["small.txt"][0] == b"CHANGED!"


def test_missing_folder_is_not_found_but_empty_volume_is_fine(tmp_path):
    fake = FakeS3()
    fake.put("other/x", b"x")
    with pytest.raises(FileNotFoundError):
        _transfer(fake, tmp_path).download("missing/", tmp_path / "out")
    empty = FakeS3()
    summary = _transfer(empty, tmp_path).download("/", tmp_path / "all")
    assert summary["files_transferred"] == 0


def test_force_transfers_unchanged_files(tmp_path):
    fake = FakeS3()
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "a.txt").write_bytes(b"alpha")
    _transfer(fake, tmp_path).upload(folder, "data")
    forced = nvt._Transfer(fake, "vol", "https://s3", 1, None, state_dir=tmp_path / "state", force=True)
    assert forced.upload(folder, "data")["files_transferred"] == 1
    out = tmp_path / "out"
    _transfer(fake, tmp_path).download("data/", out)
    forced = nvt._Transfer(fake, "vol", "https://s3", 1, None, state_dir=tmp_path / "state", force=True)
    assert forced.download("data/", out)["files_transferred"] == 1


@pytest.mark.parametrize(
    "pair",
    [("d/Model.bin", "d/model.bin"), ("d/s", "d/\u017f"), ("d/\u00e9", "d/e\u0301")],
    ids=["case", "unicode-fold", "normalization"],
)
def test_download_refuses_targets_sharing_a_local_name(tmp_path, pair):
    fake = FakeS3()
    fake.put(pair[0], b"a")
    fake.put(pair[1], b"b")
    with pytest.raises(nvt.NetworkVolumeTransferError, match="one file"):
        _transfer(fake, tmp_path).download("d/", tmp_path / "out")
    assert not fake.ops("get_object")


def test_dot_segment_key_is_refused(tmp_path):
    with pytest.raises(nvt.NetworkVolumeTransferError, match="unsafe"):
        nvt._safe_target(tmp_path, "a/./b")


def test_download_into_missing_folder_with_trailing_slash(tmp_path):
    fake = FakeS3()
    fake.put("w.bin", b"w")
    _transfer(fake, tmp_path).download("w.bin", str(tmp_path / "out") + "/")
    assert (tmp_path / "out" / "w.bin").read_bytes() == b"w"


def test_folder_upload_skips_download_leftovers(tmp_path):
    fake = FakeS3()
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "a.bin").write_bytes(b"a")
    (folder / "b.bin.rpdownload").write_bytes(b"partial")
    (folder / "b.bin.rpdownload.json").write_text("{}")
    summary = _transfer(fake, tmp_path).upload(folder, "data")
    assert summary["files_transferred"] == 1
    assert sorted(fake.objects) == ["data/a.bin"]


def test_skip_rule_compares_whole_seconds(tmp_path):
    fake = FakeS3()
    stamp = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    fake.put("f.txt", b"abc", modified=stamp)
    local = tmp_path / "f.txt"
    local.write_bytes(b"abc")
    # sub-second local precision against a whole-second server date
    os.utime(local, (stamp.timestamp(), stamp.timestamp() + 0.7))
    assert _transfer(fake, tmp_path).upload(local, "f.txt")["files_skipped"] == 1
    assert _transfer(fake, tmp_path).download("f.txt", local)["files_skipped"] == 1


def test_ctrl_c_stops_files_in_flight(tmp_path):
    fake = FakeS3()
    folder = tmp_path / "data"
    folder.mkdir()
    for i in range(6):
        (folder / f"f{i}.bin").write_bytes(DATA + bytes([i]))
    calls = []
    import threading

    lock = threading.Lock()
    original = fake.upload_part
    transfer = _transfer(fake, tmp_path, concurrency=2)

    def interrupt_first(**kwargs):
        with lock:
            calls.append(1)
            first = len(calls) == 1
        if first:
            raise KeyboardInterrupt
        # every other part waits for the interrupt to land, so what runs
        # after it is deterministic: only parts already in flight finish
        transfer._stop.wait(5)
        return original(**kwargs)

    fake.upload_part = interrupt_first
    with pytest.raises(KeyboardInterrupt):
        transfer.upload(folder, "data")
    assert transfer._stop.is_set()
    # 6 files x 5 parts would be 30 parts. the interrupted part, plus at most
    # one part per request slot that started before the interrupt landed;
    # nothing starts after it
    assert len(calls) <= 3
