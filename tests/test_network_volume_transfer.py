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


def _payload(body):
    """the bytes of a request body, read the way botocore reads a file."""
    return body.read() if hasattr(body, "read") else bytes(body)


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
        data = _payload(Body)
        self.put(Key, data)
        return {"ETag": self._etag(data)}

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
        data = _payload(Body)
        self._upload(UploadId)["parts"][PartNumber] = data
        return {"ETag": self._etag(data)}

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
def credentials(monkeypatch):
    """a logged-in user with s3 keys in the environment and nothing saved;
    tests that need otherwise change it."""
    monkeypatch.setattr(runpod, "api_key", "rpa_test")
    monkeypatch.setattr(nvt, "saved_s3_keys", lambda: (None, None))
    monkeypatch.setenv(nvt.ACCESS_KEY_ENV, "user_env")
    monkeypatch.setenv(nvt.SECRET_KEY_ENV, "rps_env")


@pytest.fixture(autouse=True)
def small_sizes(monkeypatch):
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
        _client_error("InvalidArgument", 400)
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
        _client_error("InvalidArgument", 400)
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
        _client_error("InvalidArgument", 400) if op == "upload_part" and kw["PartNumber"] == 2 else None
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
        _client_error("InvalidArgument", 400) if op == "upload_part" and kw["PartNumber"] == 2 else None
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
        _client_error("InvalidArgument", 400) if op == "complete_multipart_upload" else None
    )
    with pytest.raises(ClientError):
        _transfer(fake, tmp_path).upload(src, "big.bin")
    assert len(list((tmp_path / "state").glob("*.json"))) == 1


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
        _client_error("InvalidArgument", 400) if kw.get("Key") == "data/bad.txt" else None
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
        "data/denied.txt": _client_error("InvalidArgument", 400),
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
        _client_error("InvalidArgument", 400)
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
        _client_error("InvalidArgument", 400) if kw.get("Range") == "bytes=16-23" else None
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


def test_download_dropped_stream_is_fetched_again(tmp_path):
    from botocore.exceptions import IncompleteReadError

    fake = FakeS3()
    fake.put("f.bin", DATA)
    original = fake.get_object
    dropped = []

    class Dropped:
        def read(self):
            raise IncompleteReadError(actual_bytes=1, expected_bytes=8)

    def drop_once(**kwargs):
        response = original(**kwargs)
        if not dropped:
            dropped.append(1)
            return {"Body": Dropped(), "ETag": response["ETag"]}
        return response

    fake.get_object = drop_once
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
    fake.fail = lambda op, kw: _client_error("InvalidArgument", 400) if op == "put_object" else None
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
    monkeypatch.setattr(nvt, "_resolve_volume", lambda volume: ("vol", "EU-RO-1"))
    with pytest.raises(nvt.NetworkVolumeTransferError, match="RUNPOD_S3_ACCESS_KEY"):
        nvt._make_transfer("vol", None, None, None, 4, None)


def test_saved_s3_keys_are_the_last_fallback(monkeypatch):
    monkeypatch.delenv(nvt.ACCESS_KEY_ENV)
    monkeypatch.setattr(nvt, "saved_s3_keys", lambda: ("user_saved", "rps_saved"))
    assert nvt._s3_keys(None, None) == ("user_saved", "rps_saved")
    # a pair never mixes sources: a lone flag does not complete a pair
    assert nvt._s3_keys("user_flag", None) == ("user_saved", "rps_saved")
    assert nvt._s3_keys("user_flag", "rps_flag") == ("user_flag", "rps_flag")


def test_make_transfer_builds_datacenter_client(monkeypatch):
    # a stale local aws profile must not leak into the session
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(nvt.ACCESS_KEY_ENV, "user_x")
    monkeypatch.setenv(nvt.SECRET_KEY_ENV, "rps_x")
    monkeypatch.setattr(nvt, "_check_endpoint", lambda endpoint, datacenter: None)
    listing, calls = _list_volumes(_VOLUME)
    with listing:
        transfer = nvt._make_transfer("models", None, None, None, 4, None)
    assert calls == [("/v2/network-volumes", "networkVolumes")]
    assert transfer.client.meta.endpoint_url == "https://s3api-eu-ro-1.runpod.io/"
    assert transfer.client.meta.region_name == "EU-RO-1"
    assert transfer.client.meta.config.request_checksum_calculation == "when_required"
    # _retry_delay makes every retry decision
    assert transfer.client.meta.config.retries == {"mode": "standard", "total_max_attempts": 1}
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
        ([], "no network volume named"),
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


@pytest.mark.usefixtures("cli_preflight")
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


@pytest.mark.usefixtures("cli_preflight")
def test_cli_download_failure_says_resume():
    from runpod.rp_cli.main import cli

    with patch.object(runpod.NetworkVolume, "download", side_effect=ConnectionResetError("boom")):
        result = CliRunner().invoke(cli, ["volume", "download", "vol", "a/b"])
    assert result.exit_code != 0
    assert "boom" in result.output
    assert "re-run the same command to resume" in result.output


@pytest.mark.usefixtures("cli_preflight")
def test_cli_ctrl_c_exits_130_with_resume_hint():
    from runpod.rp_cli.main import cli

    with patch.object(runpod.NetworkVolume, "upload", side_effect=KeyboardInterrupt):
        result = CliRunner().invoke(cli, ["volume", "upload", "vol", "."])
    assert result.exit_code == 130
    assert "interrupted; re-run the same command to resume" in result.output


@pytest.mark.usefixtures("cli_preflight")
def test_cli_final_error_has_no_resume_hint():
    from runpod.rp_cli.main import cli

    err = nvt.NetworkVolumeTransferError("s3 api keys required")
    with patch.object(runpod.NetworkVolume, "download", side_effect=err):
        result = CliRunner().invoke(cli, ["volume", "download", "vol", "a/b"])
    assert result.exit_code == 1
    assert "keys required" in result.output
    assert "re-run" not in result.output


@pytest.mark.usefixtures("cli_preflight")
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

    monkeypatch.setattr(nvt, "_write_json", no_disk)
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
                "etag": nvt._etag(fake.object_etag("b.bin")),
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


def test_dns_failure_mid_transfer_is_a_network_drop():
    import socket as _socket

    from botocore.exceptions import EndpointConnectionError

    exc = EndpointConnectionError(
        endpoint_url="https://s3api-eu-ro-1.runpod.io/", error=_socket.gaierror(8, "nodename nor servname")
    )
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
    fake.fail = lambda op, kw: _client_error("InvalidArgument", 400) if op == "delete_object" else None
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


def test_single_put_change_with_older_mtime_is_resent_on_rerun(tmp_path):
    fake = FakeS3()
    src = tmp_path / "small.txt"
    src.write_bytes(b"original")
    old = src.stat().st_mtime - 3600
    os.utime(src, (old, old))
    original = fake.put_object

    def put_then_edit(**kwargs):
        result = original(**kwargs)
        # a same-size edit whose mtime is older than the object just written,
        # so the skip rule alone would keep the object
        src.write_bytes(b"CHANGED!")
        past = src.stat().st_mtime - 1800
        os.utime(src, (past, past))
        return result

    fake.put_object = put_then_edit
    with pytest.raises(nvt.NetworkVolumeTransferError, match="changed during upload") as info:
        _transfer(fake, tmp_path).upload(src, "small.txt")
    assert info.value.resumable
    assert "small.txt" not in fake.objects
    fake.put_object = original
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
        # after it is deterministic: parts in flight stop at their next read
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


def test_discarded_chunks_drop_the_sidecar_first(tmp_path):
    dest = tmp_path / "f.bin"
    job = nvt._Download(_transfer(FakeS3(), tmp_path), "f.bin", len(DATA), {"ETag": "e"}, dest)
    # a sidecar that vouches for every chunk beside a partial of the wrong size
    job.sidecar.write_text(json.dumps(
        {"key": "f.bin", "size": len(DATA), "etag": "e", "chunk_size": nvt.DOWNLOAD_CHUNK_SIZE, "done": list(range(job.count))}
    ))
    job.partial.write_bytes(b"short")
    sidecar_seen = []
    real_open = open

    def watch_open(path, *args, **kwargs):
        if path == job.partial:
            sidecar_seen.append(job.sidecar.exists())
        return real_open(path, *args, **kwargs)

    with patch("builtins.open", watch_open):
        assert job._load_or_init() == set()
    assert sidecar_seen == [False]


@pytest.mark.parametrize("suffix", nvt.TRANSFER_SUFFIXES)
def test_download_refuses_symlinked_work_files(tmp_path, suffix):
    fake = FakeS3()
    fake.put("x", DATA)  # several chunks, so the sidecar path is taken
    other = tmp_path / "other"
    other.write_bytes(b"keep me")
    try:
        (tmp_path / ("x" + suffix)).symlink_to("other")
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    with pytest.raises(nvt.NetworkVolumeTransferError, match="not a regular file") as info:
        _transfer(fake, tmp_path).download("x", tmp_path / "x")
    assert not nvt.is_resumable(info.value)
    assert other.read_bytes() == b"keep me"


def test_client_retries_the_proxy_524(monkeypatch):
    import http.server
    import threading

    hits = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server naming
            hits.append(self.path)
            if len(hits) <= 2:
                self.send_response(524)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b"<ListBucketResult></ListBucketResult>"
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        monkeypatch.setenv(nvt.ACCESS_KEY_ENV, "user_x")
        monkeypatch.setenv(nvt.SECRET_KEY_ENV, "rps_x")
        monkeypatch.setattr(nvt, "_resolve_volume", lambda volume: ("vol123", "EU-RO-1"))
        monkeypatch.setattr(nvt.random, "uniform", lambda a, b: 0)
        endpoint = f"http://127.0.0.1:{server.server_port}"
        transfer = nvt._make_transfer("vol123", None, None, endpoint, 1, None)
        transfer.client.list_objects_v2(Bucket="vol123")
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()
    assert len(hits) == 3


# ------------------------------------------------- prompts and failure cases


def _cli():
    from runpod.rp_cli.main import cli

    return cli


@pytest.fixture
def interactive(monkeypatch):
    monkeypatch.setattr("runpod.rp_cli.volume._interactive", lambda: True)


@pytest.fixture
def cli_preflight(monkeypatch):
    """Keep CLI presentation tests independent of the account and DNS."""
    monkeypatch.setattr(nvt, "_resolve_volume", lambda reference: (reference, "EU-RO-1"))
    monkeypatch.setattr(nvt, "_check_endpoint", lambda endpoint, datacenter: None)


def _fake_transfer(calls):
    """a NetworkVolume.upload/download stand-in that records its call."""

    def method(volume, source, target, **kwargs):
        calls.append((volume.reference, source, target, kwargs))
        return {"files_transferred": 1, "files_skipped": 0, "bytes_transferred": 1}

    return method


def test_cli_not_logged_in_says_how_to_log_in(monkeypatch, tmp_path):
    monkeypatch.setattr(runpod, "api_key", None)
    result = CliRunner().invoke(_cli(), ["volume", "upload", "vol", str(tmp_path)])
    assert result.exit_code == 1
    assert "not logged in: run `rp login` or set RUNPOD_API_KEY" in result.output


@pytest.mark.usefixtures("cli_preflight")
def test_cli_offers_login_and_continues(monkeypatch, tmp_path, interactive):
    import click as _click

    logins = []
    monkeypatch.setattr(runpod, "api_key", None)
    monkeypatch.setattr(
        "runpod.rp_cli.main.login",
        _click.Command("login", callback=lambda **kw: logins.append(kw)),
    )
    monkeypatch.setattr(
        "runpod.cli.groups.config.functions.get_credentials",
        lambda profile="default": {"api_key": "rpa_new"},
    )
    calls = []
    with patch.object(runpod.NetworkVolume, "upload", autospec=True, side_effect=_fake_transfer(calls)):
        result = CliRunner().invoke(_cli(), ["volume", "upload", "vol", str(tmp_path)], input="y\n")
    assert result.exit_code == 0, result.output
    assert "log in now?" in result.output
    assert len(logins) == 1 and runpod.api_key == "rpa_new" and calls


def test_cli_declined_login_stops(monkeypatch, tmp_path, interactive):
    monkeypatch.setattr(runpod, "api_key", None)
    result = CliRunner().invoke(_cli(), ["volume", "upload", "vol", str(tmp_path)], input="n\n")
    assert result.exit_code == 1
    assert "not logged in" in result.output


@pytest.mark.usefixtures("cli_preflight")
def test_cli_picks_a_volume_when_left_out(monkeypatch, tmp_path, interactive):
    volumes = [
        {"id": "aaa", "name": "first", "size": 10, "dataCenterId": "EU-RO-1"},
        {"id": "bbb", "name": "second", "size": 20, "dataCenterId": "US-KS-2"},
    ]
    monkeypatch.setattr(nvt, "_list_volumes", lambda: volumes)
    calls = []
    with patch.object(runpod.NetworkVolume, "upload", autospec=True, side_effect=_fake_transfer(calls)):
        result = CliRunner().invoke(_cli(), ["volume", "upload", str(tmp_path)], input="2\n")
    assert result.exit_code == 0, result.output
    assert "2. second  bbb  20 GB  US-KS-2" in result.output
    assert calls[0][0] == "bbb" and calls[0][1] == str(tmp_path) and calls[0][2] is None


def test_cli_without_volume_in_a_script_asks_for_it(tmp_path):
    result = CliRunner().invoke(_cli(), ["volume", "download", "outputs/"])
    assert result.exit_code == 2
    assert "pass VOLUME first" in result.output


@pytest.mark.usefixtures("cli_preflight")
def test_cli_missing_keys_in_a_script_names_every_option(monkeypatch, tmp_path):
    monkeypatch.delenv(nvt.ACCESS_KEY_ENV)
    result = CliRunner().invoke(_cli(), ["volume", "upload", "vol", str(tmp_path)])
    assert result.exit_code == 1
    for hint in ("separate from your api key", "RUNPOD_S3_ACCESS_KEY", "--s3-access-key", nvt.S3_KEYS_URL):
        assert hint in result.output


@pytest.mark.parametrize("save", [True, False])
@pytest.mark.usefixtures("cli_preflight")
def test_cli_prompts_for_keys_and_offers_to_save(monkeypatch, tmp_path, interactive, save):
    monkeypatch.delenv(nvt.ACCESS_KEY_ENV)
    saved = []
    monkeypatch.setattr(
        "runpod.cli.groups.config.functions.set_s3_keys",
        lambda access, secret, profile="default": saved.append((access, secret)),
    )
    calls = []
    with patch.object(runpod.NetworkVolume, "upload", autospec=True, side_effect=_fake_transfer(calls)):
        result = CliRunner().invoke(
            _cli(),
            ["volume", "upload", "vol", str(tmp_path)],
            input=f"user_typed\nrps_typed\n{'y' if save else 'n'}\n",
        )
    assert result.exit_code == 0, result.output
    assert calls[0][3]["access_key"] == "user_typed" and calls[0][3]["secret_key"] == "rps_typed"
    assert saved == ([("user_typed", "rps_typed")] if save else [])
    assert "rps_typed" not in result.output


def test_cli_checks_volume_before_asking_for_keys(monkeypatch, tmp_path, interactive):
    def missing_volume(reference):
        raise nvt.NetworkVolumeTransferError("no network volume named 'missing'")

    monkeypatch.setattr(nvt, "_resolve_volume", missing_volume)
    monkeypatch.setattr("runpod.rp_cli.volume._prompt_s3_keys", lambda: pytest.fail("prompted for keys"))
    result = CliRunner().invoke(_cli(), ["volume", "upload", "missing", str(tmp_path)])
    assert result.exit_code == 1
    assert "no network volume named" in result.output


def test_cli_checks_endpoint_before_asking_for_keys(monkeypatch, tmp_path, interactive):
    monkeypatch.setattr(nvt, "_resolve_volume", lambda reference: (reference, "EU-SE-1"))

    def unsupported_endpoint(endpoint, datacenter):
        raise nvt.NetworkVolumeTransferError("does not support the s3 api")

    monkeypatch.setattr(nvt, "_check_endpoint", unsupported_endpoint)
    monkeypatch.setattr("runpod.rp_cli.volume._prompt_s3_keys", lambda: pytest.fail("prompted for keys"))
    result = CliRunner().invoke(_cli(), ["volume", "upload", "vol", str(tmp_path)])
    assert result.exit_code == 1
    assert "does not support the s3 api" in result.output


@pytest.mark.usefixtures("cli_preflight")
def test_cli_offers_to_replace_rejected_saved_keys(monkeypatch, interactive, tmp_path):
    calls = []

    def transfer(volume, source, target, **kwargs):
        calls.append(kwargs["access_key"])
        if len(calls) == 1:
            raise nvt.RejectedS3Keys("rejected the keys")
        return {"files_transferred": 1, "files_skipped": 0, "bytes_transferred": 1}

    monkeypatch.delenv(nvt.ACCESS_KEY_ENV, raising=False)
    monkeypatch.delenv(nvt.SECRET_KEY_ENV, raising=False)
    monkeypatch.setattr(nvt, "saved_s3_keys", lambda: ("saved", "old"))
    monkeypatch.setattr("runpod.rp_cli.volume._prompt_s3_keys", lambda: ("new", "secret"))
    with patch.object(runpod.NetworkVolume, "upload", autospec=True, side_effect=transfer):
        result = CliRunner().invoke(
            _cli(), ["volume", "upload", "vol", str(tmp_path)], input="y\n"
        )
    assert result.exit_code == 0, result.output
    assert calls == ["saved", "new"]


def test_set_s3_keys_keeps_the_api_key_and_login_keeps_the_s3_keys(monkeypatch, tmp_path):
    from runpod.cli.groups.config import functions

    monkeypatch.setattr(functions, "CREDENTIAL_FILE", str(tmp_path / "config.toml"))
    functions.set_credentials("rpa_one", overwrite=True)
    functions.set_s3_keys("user_x", "rps_x")
    functions.set_credentials("rpa_two", overwrite=True)
    assert functions.get_credentials() == {
        "api_key": "rpa_two",
        "s3_access_key": "user_x",
        "s3_secret_key": "rps_x",
    }
    assert oct(os.stat(tmp_path / "config.toml").st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("code, status", [("403", 403), ("InvalidAccessKeyId", 403), ("SignatureDoesNotMatch", 403)])
def test_rejected_keys_say_so_and_do_not_resume(tmp_path, code, status):
    fake = FakeS3()
    fake.fail = lambda op, kw: _client_error(code, status, "HeadObject")
    src = tmp_path / "f.txt"
    src.write_text("x")
    with pytest.raises(nvt.NetworkVolumeTransferError, match="rejected the keys") as info:
        _transfer(fake, tmp_path).upload(src, None)
    assert not nvt.is_resumable(info.value)
    assert "--s3-access-key" in str(info.value)
    assert "~/.runpod/config.toml" in str(info.value)


def test_datacenter_without_the_s3_api_fails_fast(monkeypatch):
    import socket as _socket

    for name in ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("urllib.request.getproxies", lambda: {})

    def no_such_host(host, port, *args, **kwargs):
        raise _socket.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(_socket, "getaddrinfo", no_such_host)
    with pytest.raises(nvt.NetworkVolumeTransferError, match="EU-SE-1 does not support the s3 api yet") as info:
        nvt._check_endpoint("https://s3api-eu-se-1.runpod.io/", "EU-SE-1")
    assert not nvt.is_resumable(info.value)
    # behind a proxy the proxy resolves the name
    monkeypatch.setattr("urllib.request.getproxies", lambda: {"https": "http://proxy:3128"})
    monkeypatch.setattr("urllib.request.proxy_bypass", lambda host: False)
    nvt._check_endpoint("https://s3api-eu-se-1.runpod.io/", "EU-SE-1")


def test_explicit_endpoint_skips_the_datacenter_check(monkeypatch):
    checked = []
    monkeypatch.setattr(nvt, "_resolve_volume", lambda volume: ("vol123", "EU-SE-1"))
    monkeypatch.setattr(nvt, "_check_endpoint", lambda endpoint, dc: checked.append(dc))
    nvt._make_transfer("vol123", None, None, "http://localhost:9000", 1, None)
    assert checked == []
    nvt._make_transfer("vol123", None, None, None, 1, None)
    assert checked == ["EU-SE-1"]


def _response(status, code=""):
    parsed = {"ResponseMetadata": {"HTTPStatusCode": status}}
    if code:
        parsed["Error"] = {"Code": code}
    return (type("R", (), {"status_code": status})(), parsed)


def test_retry_delay_decides_every_retry(monkeypatch, tmp_path):
    from botocore.exceptions import EndpointConnectionError

    monkeypatch.setattr(nvt.random, "uniform", lambda a, b: 0)
    transfer = _transfer(FakeS3(), tmp_path)
    assert transfer._retry_delay(response=_response(524, "InternalError")) == 0
    assert transfer._retry_delay(response=_response(503)) == 0
    assert transfer._retry_delay(response=_response(429, "SlowDown")) == 0
    assert transfer._retry_delay(caught_exception=EndpointConnectionError(endpoint_url="x")) == 0
    assert transfer._retry_delay(response=_response(200)) is None
    assert transfer._retry_delay(response=_response(404, "NoSuchUpload")) is None
    assert transfer._retry_delay(response=_response(403, "AccessDenied")) is None
    assert transfer._retry_delay(response=_response(503), attempts=nvt.MAX_ATTEMPTS) is None


def test_ctrl_c_ends_a_retry_wait(tmp_path):
    transfer = _transfer(FakeS3(), tmp_path)
    transfer._stop.set()
    with pytest.raises(nvt.NetworkVolumeTransferError, match="interrupted") as info:
        transfer._retry_delay(response=_response(503))
    assert info.value.resumable
    # a successful response is never turned into an error
    assert transfer._retry_delay(response=_response(200)) is None


def test_body_reads_hold_a_request_slot(tmp_path):
    fake = FakeS3()
    fake.put("big.bin", DATA)
    transfer = _transfer(fake, tmp_path, concurrency=1)
    held = []
    original_get = fake.get_object

    def get_object(**kwargs):
        response = original_get(**kwargs)
        body = response["Body"]

        class Body:
            def read(self):
                held.append(transfer._slots._value == 0)
                return body.read()

        return {**response, "Body": Body()}

    fake.get_object = get_object
    transfer.download("big.bin", tmp_path / "out.bin")
    assert held and all(held)


def test_part_reads_hold_a_request_slot(tmp_path, monkeypatch):
    src = tmp_path / "big.bin"
    src.write_bytes(DATA)
    transfer = _transfer(FakeS3(), tmp_path, concurrency=1)
    held = []
    real_open = open

    def watch_open(path, mode="r", *args, **kwargs):
        if str(path) == str(src) and "b" in mode:
            held.append(transfer._slots._value == 0)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", watch_open)
    transfer.upload(src, None)
    assert held and all(held)


def test_cleanup_takes_a_slot_even_after_ctrl_c(tmp_path):
    fake = FakeS3()
    transfer = _transfer(fake, tmp_path, concurrency=1)
    held = []
    original_abort = fake.abort_multipart_upload
    fake.abort_multipart_upload = lambda **kw: (held.append(transfer._slots._value == 0), original_abort(**kw))
    transfer._stop.set()
    transfer._abort("k", "u")
    assert held == [True]


def test_edit_during_complete_removes_the_object(tmp_path):
    fake = FakeS3()
    src = tmp_path / "big.bin"
    src.write_bytes(DATA)

    def edit_during_complete(op, kw):
        if op == "complete_multipart_upload":
            src.write_bytes(DATA[::-1])
            later = src.stat().st_mtime + 60
            os.utime(src, (later, later))
        return None

    fake.fail = edit_during_complete
    with pytest.raises(nvt.NetworkVolumeTransferError, match="changed during upload") as info:
        _transfer(fake, tmp_path).upload(src, "big.bin")
    assert info.value.resumable
    assert "big.bin" not in fake.objects


def test_download_refuses_a_symlinked_subfolder(tmp_path):
    fake = FakeS3()
    fake.put("d/alias/x", b"1")
    fake.put("d/real/x", b"2")
    out = tmp_path / "out"
    (out / "real").mkdir(parents=True)
    try:
        (out / "alias").symlink_to("real")
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    with pytest.raises(nvt.NetworkVolumeTransferError, match="is a symlink"):
        _transfer(fake, tmp_path).download("d/", out)
    assert not (out / "real" / "x").exists()


def test_state_writes_sync_the_folder(tmp_path, monkeypatch):
    real_sync_dir = nvt._sync_dir
    synced = []
    monkeypatch.setattr(nvt, "_sync_dir", synced.append)
    nvt._write_json(tmp_path / "s" / "state.json", {"a": 1})
    assert synced == [tmp_path / "s"]
    real_sync_dir(tmp_path / "s")
    real_sync_dir(tmp_path / "missing")  # best effort: no error


def test_s3_keys_from_env_skip_the_saved_config(monkeypatch):
    monkeypatch.setenv(nvt.ACCESS_KEY_ENV, "user_x")
    monkeypatch.setenv(nvt.SECRET_KEY_ENV, "rps_x")

    def unexpected():
        raise AssertionError("the saved config was read")

    monkeypatch.setattr(nvt, "saved_s3_keys", unexpected)
    assert nvt.find_s3_keys() == ("user_x", "rps_x")


def test_upload_body_stops_mid_part(tmp_path):
    transfer = _transfer(FakeS3(), tmp_path)
    body = transfer._body(b"x" * 100)
    assert body.read(10) == b"x" * 10
    transfer._stop.set()
    with pytest.raises(nvt.NetworkVolumeTransferError, match="interrupted") as info:
        body.read(10)
    assert nvt.is_resumable(info.value)


@pytest.mark.parametrize("multipart", [False, True])
def test_upload_call_sites_stop_before_sending_body(tmp_path, monkeypatch, multipart):
    fake = FakeS3()
    transfer = _transfer(fake, tmp_path)
    src = tmp_path / "f.bin"
    src.write_bytes(b"x" * (20 if multipart else 1))
    if multipart:
        monkeypatch.setattr(nvt, "SINGLE_PUT_MAX", 8)
        original = fake.upload_part
        def interrupted(**kwargs):
            transfer._stop.set()
            return original(**kwargs)
        fake.upload_part = interrupted
    else:
        original = fake.put_object
        def interrupted(**kwargs):
            transfer._stop.set()
            return original(**kwargs)
        fake.put_object = interrupted
    with pytest.raises(nvt.NetworkVolumeTransferError, match="interrupted") as info:
        transfer.upload(src, "f.bin")
    assert nvt.is_resumable(info.value)
    assert "f.bin" not in fake.objects
    assert not any(upload["parts"] for upload in fake.uploads.values())


@pytest.mark.parametrize(
    "api_key, message", [(None, "not logged in"), ("rpa_bad", "rejected the api key")]
)
def test_api_key_missing_vs_rejected(monkeypatch, api_key, message):
    async def unauthorized(*args, **kwargs):
        raise runpod.error.AuthenticationError("Unauthorized request")

    monkeypatch.setattr(runpod, "api_key", api_key)
    monkeypatch.setattr("runpod.api.rest.run_rest_paginated_request_async", unauthorized)
    with pytest.raises(nvt.NetworkVolumeTransferError, match=message):
        nvt._resolve_volume("vol")
