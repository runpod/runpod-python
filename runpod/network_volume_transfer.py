"""resumable file and folder transfers to and from network volumes.

transfers go through the runpod s3-compatible api: the bucket is the
network volume id and the endpoint is per datacenter. call these through
NetworkVolume.upload and NetworkVolume.download.

uploads above 64 MiB use multipart. the upload id is kept in a small state
file under ~/.runpod/transfers, so re-running an interrupted upload sends
only the parts the server does not already hold. downloads fetch 64 MiB
ranges into `<dest>.rpdownload`, recording finished ranges in a sidecar
json, so a re-run fetches only what is missing. folder transfers skip
files that are already present and unchanged. each client retries failed
requests through `_Transfer._retry_delay`.
"""

import hashlib
import json
import math
import os
import random
import socket
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

T = TypeVar("T")

MIB = 1024 * 1024
SINGLE_PUT_MAX = 64 * MIB
DEFAULT_PART_SIZE = 64 * MIB
MAX_PART_SIZE = 500 * MIB  # runpod's maximum multipart part size
MAX_PARTS = 10000
DOWNLOAD_CHUNK_SIZE = 64 * MIB
# runpod's s3 api keeps in-progress multipart parts here (server-created)
UPLOADS_DIR = ".s3compat_uploads"
PARTIAL_SUFFIX = ".rpdownload"
SIDECAR_SUFFIX = ".rpdownload.json"
# every file a download writes next to its target
TRANSFER_SUFFIXES = (PARTIAL_SUFFIX, SIDECAR_SUFFIX, SIDECAR_SUFFIX + ".tmp")

# tries per request, the first included
MAX_ATTEMPTS = 10

ACCESS_KEY_ENV = "RUNPOD_S3_ACCESS_KEY"
SECRET_KEY_ENV = "RUNPOD_S3_SECRET_KEY"
S3_KEYS_URL = "https://console.runpod.io/user/settings"
S3_DOCS_URL = "https://docs.runpod.io/storage/s3-api"
NOT_LOGGED_IN = "not logged in: run `rp login` or set RUNPOD_API_KEY"
# error codes s3 gives for keys it does not accept
_REJECTED_KEY_CODES = ("InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied")

# progress(event, path, nbytes). events: "start" (nbytes = file size),
# "advance" (nbytes sent or received), "reuse" (nbytes an earlier run already
# moved: kept parts or chunks), "skip" (nbytes = file size), "done".
Progress = Callable[[str, str, int], None]


class NetworkVolumeTransferError(RuntimeError):
    """a transfer failed. `resumable` says whether re-running can help."""

    def __init__(self, message: str, resumable: bool = False):
        super().__init__(message)
        self.resumable = resumable


def part_size_for(size: int) -> int:
    """part size for a multipart upload of `size` bytes."""
    part_size = DEFAULT_PART_SIZE
    if math.ceil(size / part_size) > MAX_PARTS:
        part_size = math.ceil(math.ceil(size / MAX_PARTS) / MIB) * MIB
    if part_size > MAX_PART_SIZE:
        raise NetworkVolumeTransferError(
            f"file of {size} bytes needs parts above the 500 MiB limit"
        )
    return part_size


def _error_fields(response: Dict[str, Any]) -> Tuple[str, int]:
    """the s3 error code and http status in a parsed botocore response."""
    code = str(response.get("Error", {}).get("Code", ""))
    return code, int(response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0)


def _error(exc: BaseException) -> Tuple[str, int]:
    """the s3 error code and http status of a botocore ClientError."""
    return _error_fields(getattr(exc, "response", None) or {})


def _transient(code: str, status: int) -> bool:
    """a server answer that may pass on its own: throttling and 5xx,
    including the 524 runpod's proxy returns when an operation outlives it."""
    return (
        status == 429
        or status >= 500
        or code in ("SlowDown", "RequestTimeout", "Throttling")
    )


def _keys_rejected(exc: BaseException) -> Optional[NetworkVolumeTransferError]:
    """the error to raise when s3 refused the keys, or None."""
    code, status = _error(exc)
    if status not in (401, 403) and code not in _REJECTED_KEY_CODES:
        return None
    return NetworkVolumeTransferError(
        f"the volume's s3 api rejected the keys ({status or code}): check that "
        f"{ACCESS_KEY_ENV} and {SECRET_KEY_ENV} are current and belong to this "
        f"account (create keys at {S3_KEYS_URL})"
    )


def _is_not_found(exc: BaseException) -> bool:
    code, status = _error(exc)
    return status == 404 or code in ("404", "NoSuchKey", "NotFound")


def is_resumable(exc: BaseException) -> bool:
    """whether re-running the same transfer can get past `exc`: an
    interruption, or a network or server failure that outlasted botocore's
    retries. bad credentials, missing paths and local disk errors fail the
    same way on every run."""
    from botocore.exceptions import (  # pylint: disable=import-outside-toplevel
        ConnectionError as BotoConnectionError,
        HTTPClientError,
        IncompleteReadError,
        ResponseStreamingError,
    )

    if isinstance(exc, KeyboardInterrupt):
        return True
    if isinstance(exc, NetworkVolumeTransferError):
        return exc.resumable
    transient = (
        BotoConnectionError,
        HTTPClientError,
        IncompleteReadError,
        ResponseStreamingError,
    )
    if isinstance(exc, transient + (ConnectionError, TimeoutError)):
        return True
    # NoSuchUpload: the upload vanished (a complete whose 524 hid that it
    # finished); 416: a range that no longer fits an object that changed
    code, status = _error(exc)
    return code == "NoSuchUpload" or status == 416 or _transient(code, status)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    """replace path atomically, synced, so a crash leaves the old state or
    the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    _sync_dir(path.parent)


def _sync_dir(path: Path) -> None:
    """make a rename in path survive a power loss. best effort: windows
    cannot open a directory."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _md5_hex(path: Path, offset: int, length: int) -> str:
    digest = hashlib.md5()  # noqa: S324 - etag comparison, not security
    with open(path, "rb") as handle:
        handle.seek(offset)
        while length > 0 and (block := handle.read(min(8 * MIB, length))):
            digest.update(block)
            length -= len(block)
    return digest.hexdigest()


def _etag(value: Any) -> str:
    return str(value or "").strip('"')


def _is_md5(etag: str) -> bool:
    return len(etag) == 32 and all(c in "0123456789abcdef" for c in etag.lower())


def _multipart_etag(parts: List[Dict[str, Any]]) -> str:
    """the etag s3 (and runpod, verified) gives an object assembled from
    parts: md5 of the parts' binary md5s, then "-<count>". "" when a part
    etag is not an md5."""
    etags = [_etag(part["ETag"]) for part in parts]
    if not all(_is_md5(etag) for etag in etags):
        return ""
    digest = hashlib.md5(b"".join(bytes.fromhex(etag) for etag in etags))  # noqa: S324
    return f"{digest.hexdigest()}-{len(parts)}"


def _same_version(a: os.stat_result, b: os.stat_result) -> bool:
    return a.st_size == b.st_size and a.st_mtime_ns == b.st_mtime_ns


def _not_older(a: float, b: float) -> bool:
    """a >= b in whole seconds; s3 dates carry no finer precision."""
    return int(a) >= int(b)


def _check_remote_path(remote_path: Optional[str]) -> None:
    if remote_path and ".." in remote_path.replace("\\", "/").split("/"):
        raise ValueError(f"remote path {remote_path!r} must not contain '..'")


# the summary counter each progress event advances
_SUMMARY_FIELDS = {
    "advance": "bytes_transferred",
    "done": "files_transferred",
    "skip": "files_skipped",
}


class _Transfer:
    """one bucket on one endpoint. _make_transfer builds this."""

    def __init__(
        self,
        client: Any,
        bucket: str,
        endpoint: str,
        concurrency: int = 4,
        progress: Optional[Progress] = None,
        state_dir: Optional[Path] = None,
        force: bool = False,
    ):
        self.client = client
        self.bucket = bucket
        self.endpoint = endpoint
        self.concurrency = max(1, int(concurrency))
        self.state_dir = state_dir or Path.home() / ".runpod" / "transfers"
        # transfer every file, even ones the skip rule calls unchanged
        self.force = force
        self.summary = {
            "files_transferred": 0,
            "files_skipped": 0,
            "bytes_transferred": 0,
        }
        self._progress = progress
        self._lock = threading.Lock()
        # folder transfers run files in parallel and each large file runs its
        # parts in parallel; this bounds the requests in flight across both
        self._slots = threading.BoundedSemaphore(self.concurrency)
        # set on ctrl-c so queued work stops instead of finishing files in flight
        self._stop = threading.Event()

    def _request(self, work: Callable[[], T]) -> T:
        """run work, one request plus its body or part read, holding a slot,
        so `concurrency` also bounds the bytes in flight."""
        with self._slots:
            if self._stop.is_set():
                raise NetworkVolumeTransferError("interrupted", resumable=True)
            try:
                return work()
            except Exception as exc:
                rejected = _keys_rejected(exc)
                if rejected is None:
                    raise
                raise rejected from exc

    def _client_call(self, method: str, **kwargs: Any) -> Any:
        return getattr(self.client, method)(Bucket=self.bucket, **kwargs)

    def _call(self, method: str, **kwargs: Any) -> Any:
        return self._request(lambda: self._client_call(method, **kwargs))

    def _cleanup(self, method: str, **kwargs: Any) -> Any:
        """a cleanup request (delete, abort). it takes a slot but skips the
        stop check, so it still runs after ctrl-c."""
        with self._slots:
            return self._client_call(method, **kwargs)

    def _retry_delay(
        self, response=None, caught_exception=None, attempts=1, **_kwargs
    ) -> Optional[float]:
        """botocore's needs-retry hook for this transfer's client, whose own
        retries are off: botocore's standard mode skips 524 and shares one
        retry quota across a whole run. waits here, so ctrl-c ends the wait,
        and returns 0 to retry or None not to."""
        if caught_exception is not None:
            failed = is_resumable(caught_exception)
        else:
            failed = response is not None and _transient(*_error_fields(response[1]))
        if not failed or attempts >= MAX_ATTEMPTS:
            return None
        if self._stop.wait(random.uniform(0, min(20.0, 2.0**attempts))):
            raise NetworkVolumeTransferError("interrupted", resumable=True)
        return 0

    def _run(self, tasks: List[Callable[[], Any]]) -> List[Any]:
        """run tasks in parallel; the first failure cancels the ones not started."""
        pool = ThreadPoolExecutor(max_workers=self.concurrency)
        try:
            return [
                future.result()
                for future in as_completed([pool.submit(t) for t in tasks])
            ]
        except BaseException as exc:
            if not isinstance(exc, Exception):
                self._stop.set()
            raise
        finally:
            pool.shutdown(cancel_futures=True)

    def _emit(self, event: str, path: str, nbytes: int = 0) -> None:
        field = _SUMMARY_FIELDS.get(event)
        if field is not None:
            with self._lock:
                self.summary[field] += nbytes if event == "advance" else 1
        if self._progress is not None:
            self._progress(event, path, nbytes)

    def _run_files(self, jobs: List[Tuple[Any, Callable[[], Any]]]) -> None:
        """run every file; failures are reported together, resumable only
        when every one of them is."""
        failures: List[Tuple[str, bool]] = []

        def guarded(label: Any, task: Callable[[], Any]) -> None:
            try:
                task()
            except Exception as exc:  # noqa: BLE001 - collected and re-raised
                with self._lock:
                    failures.append((f"{label}: {exc}", is_resumable(exc)))

        self._run([lambda l=label, t=task: guarded(l, t) for label, task in jobs])
        if failures:
            raise NetworkVolumeTransferError(
                f"{len(failures)} file(s) failed:\n  "
                + "\n  ".join(sorted(m for m, _ in failures)),
                resumable=all(ok for _, ok in failures),
            )

    # -- remote metadata

    def head(self, key: str, directory_ok: bool = False) -> Optional[Dict[str, Any]]:
        """object metadata, or None when there is no file at `key`. with
        `directory_ok`, a 400 also means "not a file": runpod answers
        HeadObject on a directory with 400 rather than 404."""
        try:
            return self._call("head_object", Key=key)
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc) or (directory_ok and _error(exc)[1] == 400):
                return None
            raise

    def _pages(
        self, method: str, items: str, marker: str, arg: str, **kwargs: Any
    ) -> List[Any]:
        found: List[Any] = []
        while True:
            page = self._call(method, **kwargs)
            found.extend(page.get(items) or [])
            if not page.get("IsTruncated") or not page.get(marker):
                return found
            kwargs[arg] = page[marker]

    def list_files(self, prefix: str) -> List[Dict[str, Any]]:
        """the files under prefix, without folder markers and in-progress
        multipart parts."""
        try:
            listed = self._pages(
                "list_objects_v2",
                "Contents",
                "NextContinuationToken",
                "ContinuationToken",
                Prefix=prefix,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return []
            raise
        return [
            obj
            for obj in listed
            if not obj["Key"].endswith("/")
            and UPLOADS_DIR not in PurePosixPath(obj["Key"]).parts
        ]

    def _is_folder(self, key: str) -> bool:
        """whether something is listed under `key/` (runpod lists even an
        empty folder's own marker)."""
        try:
            return bool(
                self._call("list_objects_v2", Prefix=f"{key}/", MaxKeys=1).get(
                    "Contents"
                )
            )
        except Exception:  # noqa: BLE001 - unknown: report the original error
            return False

    # -- upload

    def upload(self, local: Path, remote_path: Optional[str]) -> Dict[str, int]:
        _check_remote_path(remote_path)
        if not local.exists():
            raise FileNotFoundError(str(local))
        if local.is_dir():
            self._upload_folder(local, remote_path)
        else:
            self._upload_one(local, remote_path)
        return self.summary

    def _upload_folder(self, local: Path, remote_path: Optional[str]) -> None:
        prefix = (local.resolve().name if remote_path is None else remote_path).strip(
            "/"
        )
        remote = {
            obj["Key"]: obj for obj in self.list_files(f"{prefix}/" if prefix else "")
        }
        jobs = []
        for path in sorted(p for p in local.rglob("*") if p.is_file()):
            key = path.relative_to(local).as_posix()
            key = f"{prefix}/{key}" if prefix else key
            # an interrupted download's working files are not data
            if path.name.endswith(TRANSFER_SUFFIXES) or self._unchanged_remote(
                remote.get(key), path
            ):
                self._emit("skip", str(path), path.stat().st_size)
            else:
                jobs.append((path, lambda p=path, k=key: self.upload_file(p, k)))
        self._run_files(jobs)

    def _upload_one(self, local: Path, remote_path: Optional[str]) -> None:
        # a remote path ending in "/" keeps the local name inside that folder
        key = local.name if remote_path is None else remote_path.lstrip("/")
        if remote_path is not None and remote_path.endswith("/"):
            key += local.name
        try:
            remote = self.head(key)
        except Exception as exc:  # noqa: BLE001
            # runpod answers a head on a directory with 400, but a head has no
            # body, so a 400 for any other reason looks the same: confirm
            if _error(exc)[1] == 400 and self._is_folder(key):
                raise NetworkVolumeTransferError(
                    f"{key!r} is a folder on the volume; "
                    f"use {key + '/'!r} to upload into it"
                ) from exc
            raise
        if self._unchanged_remote(remote, local):
            self._emit("skip", str(local), local.stat().st_size)
            return
        self.upload_file(local, key)

    def _unchanged_remote(self, remote: Optional[Dict[str, Any]], local: Path) -> bool:
        if self.force or not remote or remote.get("LastModified") is None:
            return False
        stat = local.stat()
        size = remote.get("Size", remote.get("ContentLength"))
        if size is None or int(size) != stat.st_size:
            return False
        return _not_older(remote["LastModified"].timestamp(), stat.st_mtime)

    def upload_file(self, local: Path, key: str) -> None:
        stat = local.stat()
        self._emit("start", str(local), stat.st_size)
        if stat.st_size <= SINGLE_PUT_MAX:
            self._single_put(local, key, stat)
        else:
            _Upload(self, local, key, stat).run()
        self._emit("done", str(local))

    def _single_put(self, local: Path, key: str, stat: os.stat_result) -> None:
        self._request(
            lambda: self._client_call("put_object", Key=key, Body=local.read_bytes())
        )
        self._keep_if_unchanged(local, key, stat)
        self._emit("advance", str(local), stat.st_size)

    def _keep_if_unchanged(self, local: Path, key: str, stat: os.stat_result) -> None:
        """after an upload, check the file did not change while it was read."""
        try:
            now = local.stat()
        except OSError as exc:
            # gone or unreadable: the object is what was read, keep it
            raise NetworkVolumeTransferError(
                f"{local} is gone after its upload: {exc}"
            ) from exc
        if not _same_version(stat, now):
            self._delete_changed(local, key)

    def _delete_changed(self, local: Path, key: str) -> None:
        """the file changed while it was read, so the object may hold either
        version and is newer than the local edit; a re-run would skip it.
        remove it so the re-run sends the file."""
        try:
            self._cleanup("delete_object", Key=key)
        except Exception as exc:  # noqa: BLE001
            raise NetworkVolumeTransferError(
                f"{local} changed during upload and the remote copy could not be "
                f"removed ({exc}); re-run with --force"
            ) from exc
        raise NetworkVolumeTransferError(
            f"{local} changed during upload", resumable=True
        )

    def _abort(self, key: str, upload_id: str) -> None:
        """discard an upload that can no longer be resumed (best effort: a
        failed abort only leaves orphaned parts behind)."""
        try:
            self._cleanup("abort_multipart_upload", Key=key, UploadId=upload_id)
        except Exception:  # noqa: BLE001
            return

    # -- download

    def download(self, remote_path: str, local_path: Any = None) -> Dict[str, int]:
        """`local_path` is a str or Path; a str ending in "/" is a folder to
        download into (a Path cannot carry the slash)."""
        _check_remote_path(remote_path)
        key = remote_path.lstrip("/")
        head = (
            None
            if key == "" or key.endswith("/")
            else self.head(key, directory_ok=True)
        )
        if head is None:
            self._download_folder(key.rstrip("/"), remote_path, local_path)
            return self.summary
        name = PurePosixPath(key).name
        dest = Path(local_path) if local_path is not None else Path(name)
        # a trailing slash names a folder to download into, as on upload
        if dest.is_dir() or str(local_path or "").endswith(("/", os.sep)):
            dest = dest / name
        _Download(self, key, head["ContentLength"], head, dest).run()
        return self.summary

    def _download_folder(self, prefix: str, remote_path: str, local_path: Any) -> None:
        listed = f"{prefix}/" if prefix else ""
        objects = self.list_files(listed)
        # a typo must not look like an empty folder; the whole volume may be empty
        if not objects and prefix:
            raise FileNotFoundError(
                f"no such file or directory on the volume: {remote_path}"
            )
        root = (
            Path(local_path)
            if local_path is not None
            else Path(PurePosixPath(prefix).name or self.bucket)
        )
        jobs = [(_safe_target(root, obj["Key"][len(listed) :]), obj) for obj in objects]
        _check_local_names([dest for dest, _ in jobs], root)
        self._run_files(
            [
                (
                    dest,
                    lambda d=dest, o=obj: _Download(
                        self, o["Key"], o["Size"], o, d
                    ).run(),
                )
                for dest, obj in jobs
            ]
        )


class _Upload:
    """one multipart upload of a local file, pinned to its size and mtime."""

    def __init__(
        self, transfer: _Transfer, local: Path, key: str, stat: os.stat_result
    ):
        self.t = transfer
        self.local = local
        self.key = key
        self.stat = stat
        self.part_size = part_size_for(stat.st_size)
        self.count = math.ceil(stat.st_size / self.part_size)
        ident = f"{transfer.endpoint}|{transfer.bucket}|{key}|{local.resolve()}"
        self.state_path = transfer.state_dir / (
            hashlib.sha256(ident.encode()).hexdigest() + ".json"
        )
        self.upload_id = ""
        self.parts: Dict[int, str] = {}  # part number -> etag

    def run(self) -> None:
        if not self._resume():
            self._create()
        reused = sum(self._length(n) for n in self.parts)
        if reused:
            self.t._emit("reuse", str(self.local), reused)
        todo = [n for n in range(1, self.count + 1) if n not in self.parts]
        self.parts.update(self.t._run([lambda n=n: self._send(n) for n in todo]))
        # the parts were read over the whole upload; if the file moved under
        # them, they may mix two versions of it
        if not _same_version(self.stat, self.local.stat()):
            self.t._abort(self.key, self.upload_id)
            self.state_path.unlink(missing_ok=True)
            raise NetworkVolumeTransferError(
                f"{self.local} changed during upload", resumable=True
            )
        self._complete()
        self.state_path.unlink(missing_ok=True)
        # completing can take long enough for an edit to land meanwhile
        self.t._keep_if_unchanged(self.local, self.key, self.stat)

    def _length(self, number: int) -> int:
        return min(self.part_size, self.stat.st_size - (number - 1) * self.part_size)

    def _state(self) -> Dict[str, Any]:
        return {
            "upload_id": self.upload_id,
            "part_size": self.part_size,
            "file_size": self.stat.st_size,
            "mtime_ns": self.stat.st_mtime_ns,
            "bucket": self.t.bucket,
            "key": self.key,
        }

    def _resume(self) -> bool:
        """pick up the saved upload for this file with its verified parts;
        false when there is none to resume."""
        state = _read_json(self.state_path) or {}
        upload_id = state.get("upload_id")
        if not upload_id:
            return False
        pinned = (self.part_size, self.stat.st_size, self.stat.st_mtime_ns)
        if (
            state.get("part_size"),
            state.get("file_size"),
            state.get("mtime_ns"),
        ) != pinned:
            # the local file changed since the upload began: its parts are stale
            self.t._abort(self.key, upload_id)
            self.state_path.unlink(missing_ok=True)
            return False
        try:
            listed = self.t._pages(
                "list_parts",
                "Parts",
                "NextPartNumberMarker",
                "PartNumberMarker",
                Key=self.key,
                UploadId=upload_id,
            )
        except Exception as exc:  # noqa: BLE001
            if _error(exc)[0] != "NoSuchUpload" and not _is_not_found(exc):
                raise
            # the server no longer has this upload: start a new one
            self.state_path.unlink(missing_ok=True)
            return False
        self.upload_id = upload_id
        self.parts = {
            int(p["PartNumber"]): p["ETag"] for p in listed if self._part_matches(p)
        }
        return True

    def _part_matches(self, part: Dict[str, Any]) -> bool:
        """whether a listed part holds this file's bytes. a part whose
        content cannot be verified is uploaded again."""
        number = int(part["PartNumber"])
        length = self._length(number)
        etag = _etag(part.get("ETag"))
        if length <= 0 or int(part.get("Size", -1)) != length or not _is_md5(etag):
            return False
        return (
            _md5_hex(self.local, (number - 1) * self.part_size, length) == etag.lower()
        )

    def _create(self) -> None:
        self.upload_id = self.t._call("create_multipart_upload", Key=self.key)[
            "UploadId"
        ]
        self.parts = {}
        try:
            _write_json(self.state_path, self._state())
        except OSError:
            # without saved state no re-run can find this upload again
            self.t._abort(self.key, self.upload_id)
            raise

    def _send(self, number: int) -> Tuple[int, str]:
        response = self.t._request(lambda: self._read_and_send(number))
        self.t._emit("advance", str(self.local), self._length(number))
        return number, response["ETag"]

    def _read_and_send(self, number: int) -> Dict[str, Any]:
        with open(self.local, "rb") as handle:
            handle.seek((number - 1) * self.part_size)
            body = handle.read(self._length(number))
        return self.t._client_call(
            "upload_part",
            Key=self.key,
            UploadId=self.upload_id,
            PartNumber=number,
            Body=body,
        )

    def _complete(self) -> None:
        parts = [{"PartNumber": n, "ETag": self.parts[n]} for n in sorted(self.parts)]
        try:
            self.t._call(
                "complete_multipart_upload",
                Key=self.key,
                UploadId=self.upload_id,
                MultipartUpload={"Parts": parts},
            )
        except Exception as exc:  # noqa: BLE001 - re-raised unless it completed
            if not self._completed_anyway(exc, _multipart_etag(parts)):
                raise

    def _completed_anyway(self, exc: Exception, want_etag: str) -> bool:
        """whether a failed complete took effect anyway. assembling a large
        file can outlive the proxy (524) while it still finishes server-side,
        and a retry then finds the upload gone. only those ambiguous failures
        qualify, and only when the object at the key is this upload's: its
        size matches and its etag equals the multipart etag of our parts."""
        if not want_etag or not (is_resumable(exc) or _is_not_found(exc)):
            return False
        head = self.t.head(self.key)
        return bool(
            head
            and int(head.get("ContentLength", -1)) == self.stat.st_size
            and _etag(head.get("ETag")) == want_etag
        )


class _Download:
    """one file download into `<dest>.rpdownload`, with its finished chunks
    recorded in a sidecar json."""

    def __init__(
        self, transfer: _Transfer, key: str, size: Any, meta: Dict[str, Any], dest: Path
    ):
        self.t = transfer
        self.key = key
        self.size = int(size)
        # the etag is only compared for equality: a multipart object's etag is
        # "<md5>-<parts>", never the md5 of the file
        self.etag = _etag(meta.get("ETag"))
        self.modified = meta.get("LastModified")
        self.dest = dest
        self.partial = dest.with_name(dest.name + PARTIAL_SUFFIX)
        self.sidecar = dest.with_name(dest.name + SIDECAR_SUFFIX)
        self.count = math.ceil(self.size / DOWNLOAD_CHUNK_SIZE)
        self.done: set = set()

    def run(self) -> None:
        label = str(self.dest)
        if self._unchanged_local():
            self.t._emit("skip", label, self.size)
            return
        self.t._emit("start", label, self.size)
        self.dest.parent.mkdir(parents=True, exist_ok=True)
        self._refuse_linked_work_files()
        self.done = self._load_or_init()
        reused = sum(self._length(i) for i in self.done)
        if reused:
            self.t._emit("reuse", label, reused)
        self.t._run(
            [
                lambda i=i: self._fetch(i)
                for i in range(self.count)
                if i not in self.done
            ]
        )
        if self.partial.stat().st_size != self.size:
            raise NetworkVolumeTransferError(
                f"{self.dest}: size mismatch after download", resumable=True
            )
        os.replace(self.partial, self.dest)
        if self.modified is not None:
            # stamp the remote time so uploading the file back counts as unchanged
            os.utime(self.dest, (time.time(), self.modified.timestamp()))
        self.sidecar.unlink(missing_ok=True)
        self.t._emit("done", label)

    def _length(self, index: int) -> int:
        return min(DOWNLOAD_CHUNK_SIZE, self.size - index * DOWNLOAD_CHUNK_SIZE)

    def _unchanged_local(self) -> bool:
        """downloads stamp the remote time, so an unchanged file is the same
        size and not older than the remote copy."""
        if (
            self.t.force
            or not self.dest.is_file()
            or self.dest.stat().st_size != self.size
        ):
            return False
        return self.modified is None or _not_older(
            self.dest.stat().st_mtime, self.modified.timestamp()
        )

    def _refuse_linked_work_files(self) -> None:
        """refuse a working file that exists as anything but a regular file:
        a symlink would aim the write elsewhere."""
        for suffix in TRANSFER_SUFFIXES:
            path = self.dest.with_name(self.dest.name + suffix)
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise NetworkVolumeTransferError(
                    f"{path} exists and is not a regular file; remove it and rerun"
                )

    def _state(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "size": self.size,
            "etag": self.etag,
            "chunk_size": DOWNLOAD_CHUNK_SIZE,
            "done": sorted(self.done),
        }

    def _load_or_init(self) -> set:
        """the chunks an earlier run finished, or a fresh partial file."""
        state = _read_json(self.sidecar) or {}
        saved = state.pop("done", [])
        if (
            state == {k: v for k, v in self._state().items() if k != "done"}
            and self.partial.is_file()
            and self.partial.stat().st_size == self.size
        ):
            return {int(i) for i in saved if 0 <= int(i) < self.count}
        # drop the old sidecar first: a stop between emptying the partial and
        # saving fresh state must not leave it vouching for discarded chunks
        self.sidecar.unlink(missing_ok=True)
        with open(self.partial, "wb") as handle:
            handle.truncate(self.size)
        _write_json(self.sidecar, self._state())
        return set()

    def _fetch(self, index: int) -> None:
        start = index * DOWNLOAD_CHUNK_SIZE
        length = self._length(index)
        data = self._get_range(start, length)
        with open(self.partial, "r+b") as handle:
            handle.seek(start)
            handle.write(data)
            handle.flush()
            # on disk before the sidecar claims it
            os.fsync(handle.fileno())
        with self.t._lock:
            self.done.add(index)
            _write_json(self.sidecar, self._state())
        self.t._emit("advance", str(self.dest), length)

    def _get_range(self, start: int, length: int) -> bytes:
        """botocore retries the request but not the body read, so a dropped
        or short body is fetched again (three tries)."""
        for _ in range(3):
            data = self.t._request(lambda: self._read_range(start, length))
            if data is not None and len(data) == length:
                return data
        raise NetworkVolumeTransferError(
            f"could not read {self.key} bytes {start}-{start + length - 1}",
            resumable=True,
        )

    def _read_range(self, start: int, length: int) -> Optional[bytes]:
        """one GET and its body read; None when the body dropped."""
        response = self.t._client_call(
            "get_object", Key=self.key, Range=f"bytes={start}-{start + length - 1}"
        )
        if self.etag and _etag(response.get("ETag")) not in ("", self.etag):
            raise NetworkVolumeTransferError(
                f"{self.key} changed on the volume during download; "
                "run the command again to restart it",
                resumable=True,
            )
        try:
            return response["Body"].read()
        except Exception as exc:  # noqa: BLE001 - a dropped stream is retried
            if not is_resumable(exc):
                raise
            return None


def _check_local_names(targets: List[Path], root: Path) -> None:
    """refuse targets that would share a path on disk: two that are one name
    on a case-insensitive or normalizing filesystem ("Model.bin" and
    "model.bin"), a file whose name is also a folder another file lives in
    ("a" beside "A/b"), or a file whose partial, sidecar or sidecar temp name
    is also a target or a folder one lives in ("x" beside "x.rpdownload/y").
    names are compared nfc-normalized (apfs ignores normalization) and case
    folded, which can refuse a listing a case-sensitive filesystem would
    take: the safe direction."""

    def fold(path: PurePosixPath) -> str:
        return unicodedata.normalize("NFC", str(path)).casefold()

    rels = [PurePosixPath(target.relative_to(root).as_posix()) for target in targets]
    files: Dict[str, PurePosixPath] = {}
    dirs: Dict[str, PurePosixPath] = {}
    for rel in rels:
        if fold(rel) in files:
            raise NetworkVolumeTransferError(
                f"cannot download both {files[fold(rel)]} and {rel}: they would be one "
                "file on a case-insensitive or normalizing filesystem"
            )
        files[fold(rel)] = rel
        dirs.update({fold(parent): rel for parent in rel.parents if str(parent) != "."})
    for rel in rels:
        if fold(rel) in dirs:
            raise NetworkVolumeTransferError(
                f"cannot download both {rel} and {dirs[fold(rel)]}: "
                "the first would have to be a file and a folder"
            )
        for suffix in TRANSFER_SUFFIXES:
            twin = fold(rel.with_name(rel.name + suffix))
            other = files.get(twin) or dirs.get(twin)
            if other is not None:
                raise NetworkVolumeTransferError(
                    f"cannot download both {rel} and {other}: "
                    f"{rel.name + suffix} is used for the first's partial download"
                )


def _safe_target(root: Path, rel: str) -> Path:
    """local path for a remote relative key, refusing anything outside root.
    "." and empty segments are refused too: "a/./b" would alias "a/b"."""
    segments = rel.split("/")
    if (
        rel.startswith("/")
        or "\\" in rel
        or any(seg in ("..", ".", "") for seg in segments)
        or ":" in segments[0]
    ):
        raise NetworkVolumeTransferError(f"refusing unsafe remote path: {rel!r}")
    target = root.joinpath(*segments)
    if root.resolve() not in target.resolve().parents:
        raise NetworkVolumeTransferError(f"refusing unsafe remote path: {rel!r}")
    # a symlinked folder would let two remote paths share one local file,
    # which the name collision check cannot see
    for depth in range(1, len(segments)):
        if root.joinpath(*segments[:depth]).is_symlink():
            raise NetworkVolumeTransferError(
                f"{root.joinpath(*segments[:depth])} is a symlink; "
                "download into a folder without symlinked subfolders"
            )
    return target


# ---------------------------------------------------------------- client


def _list_volumes() -> List[Dict[str, Any]]:
    """every network volume of the account."""
    from runpod import error  # pylint: disable=import-outside-toplevel
    from runpod.api.rest import (  # pylint: disable=import-outside-toplevel
        run_rest_paginated_request_async,
    )
    from runpod.apps.context import block  # pylint: disable=import-outside-toplevel

    try:
        return block(
            run_rest_paginated_request_async("/v2/network-volumes", "networkVolumes")
        )
    except error.AuthenticationError as exc:
        raise NetworkVolumeTransferError(NOT_LOGGED_IN) from exc


def _resolve_volume(reference: str) -> Tuple[str, str]:
    """the id and datacenter of the network volume with this id or name."""
    from runpod.apps.utils.lookup import (  # pylint: disable=import-outside-toplevel
        find_by_id_or_name,
    )

    record = find_by_id_or_name(
        _list_volumes(),
        reference,
        noun="network volumes",
        error=NetworkVolumeTransferError,
    )
    if record is None:
        raise NetworkVolumeTransferError(
            f"no network volume named {reference!r}: pass a volume id or name "
            "(see https://console.runpod.io/user/storage)"
        )
    datacenter = record.get("dataCenter") or record.get("dataCenterId")
    if not datacenter:
        raise NetworkVolumeTransferError(
            f"could not find the datacenter of network volume {reference!r}"
        )
    return record["id"], str(datacenter)


def saved_s3_keys() -> Tuple[Optional[str], Optional[str]]:
    """the s3 keys saved in ~/.runpod/config.toml for the current profile."""
    import runpod  # pylint: disable=import-outside-toplevel,cyclic-import
    from runpod.cli.groups.config.functions import (  # pylint: disable=import-outside-toplevel
        get_credentials,
    )

    saved = get_credentials(runpod.profile) or {}
    return saved.get("s3_access_key"), saved.get("s3_secret_key")


def find_s3_keys(
    access_key: Optional[str] = None, secret_key: Optional[str] = None
) -> Optional[Tuple[str, str]]:
    """the first complete pair of s3 keys from the arguments, the
    environment, or the saved config; None when there is none. a pair never
    mixes sources."""
    pairs = (
        (access_key, secret_key),
        (os.environ.get(ACCESS_KEY_ENV), os.environ.get(SECRET_KEY_ENV)),
        saved_s3_keys(),
    )
    return next(((a, s) for a, s in pairs if a and s), None)


def _s3_keys(access_key: Optional[str], secret_key: Optional[str]) -> Tuple[str, str]:
    keys = find_s3_keys(access_key, secret_key)
    if keys is None:
        raise NetworkVolumeTransferError(
            f"s3 api keys required: set {ACCESS_KEY_ENV} and {SECRET_KEY_ENV} "
            f"or pass access_key and secret_key (create keys at {S3_KEYS_URL})"
        )
    return keys


def _check_endpoint(endpoint: str, datacenter: str) -> None:
    """fail fast on a datacenter without the s3 api: its endpoint does not
    resolve, and retrying would only delay the error. behind an https proxy
    the proxy resolves the name, so the check is skipped."""
    from urllib.parse import urlparse  # pylint: disable=import-outside-toplevel
    from urllib.request import (  # pylint: disable=import-outside-toplevel
        getproxies,
        proxy_bypass,
    )

    host = urlparse(endpoint).hostname or ""
    if getproxies().get("https") and not proxy_bypass(host):
        return
    try:
        socket.getaddrinfo(host, 443)
    except socket.gaierror as exc:
        raise NetworkVolumeTransferError(
            f"{datacenter} does not support the s3 api yet ({S3_DOCS_URL}), "
            f"or {host} is unreachable from this network"
        ) from exc


def _client_config(concurrency: int) -> Any:
    from botocore.config import Config  # pylint: disable=import-outside-toplevel

    return Config(
        s3={"addressing_style": "path"},
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        # one try: _Transfer._retry_delay makes every retry decision
        retries={"mode": "standard", "total_max_attempts": 1},
        connect_timeout=60,
        # a socket read silent this long is a stalled connection (runpod's
        # proxy answers a slow request with 524 after about 100s)
        read_timeout=300,
        # headroom over the request bound for connections being returned
        max_pool_connections=max(10, 2 * concurrency),
    )


def _make_transfer(
    volume: str,
    access_key: Optional[str],
    secret_key: Optional[str],
    endpoint_url: Optional[str],
    concurrency: int,
    progress: Optional[Progress],
    force: bool = False,
) -> _Transfer:
    """a transfer to the network volume with this id or name. backs
    NetworkVolume.upload and NetworkVolume.download."""
    import boto3  # pylint: disable=import-outside-toplevel

    volume_id, datacenter = _resolve_volume(volume)
    access_key, secret_key = _s3_keys(access_key, secret_key)
    endpoint = endpoint_url or f"https://s3api-{datacenter.lower()}.runpod.io/"
    if not endpoint_url:
        _check_endpoint(endpoint, datacenter)
    concurrency = max(1, int(concurrency))
    client = boto3.session.Session().client(
        "s3",
        region_name=datacenter,
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=_client_config(concurrency),
    )
    transfer = _Transfer(client, volume_id, endpoint, concurrency, progress, force=force)
    client.meta.events.register_first("needs-retry.s3", transfer._retry_delay)
    return transfer
