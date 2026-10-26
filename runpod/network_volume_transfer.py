"""resumable file and folder transfers to and from network volumes.

transfers go through the runpod s3-compatible api. the bucket is the
network volume id and the endpoint is per datacenter. call these
through NetworkVolume.upload and NetworkVolume.download.

uploads above 64 MiB use multipart. the upload id is kept in a small
state file under ~/.runpod/transfers, so re-running an interrupted
upload sends only the parts the server does not already hold. downloads
fetch 64 MiB ranges in parallel into `<dest>.rpdownload`, recording
finished ranges in a sidecar json, so a re-run fetches only what is
missing. folder transfers skip files that are already present and
unchanged.
"""

import hashlib
import json
import math
import os
import random
import socket
import threading
import unicodedata
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Tuple

MIB = 1024 * 1024
SINGLE_PUT_MAX = 64 * MIB
DEFAULT_PART_SIZE = 64 * MIB
MAX_PART_SIZE = 500 * MIB  # runpod's maximum multipart part size
MAX_PARTS = 10000
DOWNLOAD_CHUNK_SIZE = 64 * MIB
RETRY_ATTEMPTS = 8
# runpod's s3 api keeps in-progress multipart parts here (server-created)
UPLOADS_DIR = ".s3compat_uploads"
PARTIAL_SUFFIX = ".rpdownload"
SIDECAR_SUFFIX = ".rpdownload.json"
# every file a download writes next to its target
TRANSFER_SUFFIXES = (PARTIAL_SUFFIX, SIDECAR_SUFFIX, SIDECAR_SUFFIX + ".tmp")

ACCESS_KEY_ENV = "RUNPOD_S3_ACCESS_KEY"
SECRET_KEY_ENV = "RUNPOD_S3_SECRET_KEY"
S3_KEYS_URL = "https://console.runpod.io/user/settings"

# progress(event, path, nbytes). events: "start" (nbytes = file size),
# "advance" (nbytes sent or received), "reuse" (nbytes an earlier run already
# moved: kept parts or chunks), "skip" (nbytes = file size), "done".
Progress = Callable[[str, str, int], None]

_sleep = time.sleep


class NetworkVolumeTransferError(RuntimeError):
    """a transfer failed. `resumable` says whether re-running can help."""

    def __init__(self, message: str, resumable: bool = False):
        super().__init__(message)
        self.resumable = resumable


class _Retryable(Exception):
    """a transient failure raised by this module (e.g. a short read)."""


class _Stopped(Exception):
    """the transfer was interrupted; raised by workers so they wind down."""


def _state_dir() -> Path:
    return Path(os.path.expanduser("~/.runpod/transfers"))


# --------------------------------------------------------------- helpers


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


def _error_code(exc: Exception) -> Tuple[str, int]:
    response = getattr(exc, "response", None) or {}
    code = str(response.get("Error", {}).get("Code", ""))
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0
    return code, int(status)


def _is_not_found(exc: Exception) -> bool:
    code, status = _error_code(exc)
    return status == 404 or code in ("404", "NoSuchKey", "NotFound")


def _is_retryable(exc: Exception) -> bool:
    from botocore.exceptions import (  # pylint: disable=import-outside-toplevel
        ClientError,
        ConnectionError as BotoConnectionError,
        HTTPClientError,
        IncompleteReadError,
        ResponseStreamingError,
    )

    if isinstance(exc, _Retryable):
        return True
    if isinstance(
        exc,
        (
            BotoConnectionError,
            HTTPClientError,
            IncompleteReadError,
            ResponseStreamingError,
            ConnectionError,
            TimeoutError,
        ),
    ):
        return True
    if isinstance(exc, ClientError):
        code, status = _error_code(exc)
        if status == 429 or status >= 500:
            return True
        return code in ("SlowDown", "RequestTimeout", "InternalError", "524")
    return False


def is_resumable(exc: BaseException) -> bool:
    """whether re-running the same transfer can get past `exc`.

    an interruption, or a transient failure (network, timeout, 429, 5xx)
    that outlasted the retries, resumes. anything else (missing paths, bad
    credentials, a wrong datacenter, local disk errors, invalid input) fails
    the same way on every run.
    """
    if isinstance(exc, KeyboardInterrupt):
        return True
    if isinstance(exc, NetworkVolumeTransferError):
        return exc.resumable
    # final-looking errors a re-run gets past: the multipart upload vanished
    # (e.g. after a complete whose 524 hid that it finished), or a range that
    # no longer fits an object that changed mid-download
    code, status = _error_code(exc)
    if code == "NoSuchUpload" or status == 416:
        return True
    return _is_retryable(exc)


def _wait(delay: float, stop: Optional[threading.Event] = None) -> None:
    """sleep for a retry backoff, cut short when `stop` is set."""
    if stop is None:
        _sleep(delay)
    else:
        stop.wait(delay)


def _with_retries(
    func: Callable[[], Any],
    attempts: int = RETRY_ATTEMPTS,
    stop: Optional[threading.Event] = None,
) -> Any:
    """call func, retrying transient failures with exponential backoff. a set
    `stop` (ctrl-c) ends a backoff early; the next attempt then raises."""
    for attempt in range(attempts):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - filtered by _is_retryable
            if attempt == attempts - 1 or not _is_retryable(exc):
                raise
            _wait(min(2**attempt, 30) + random.uniform(0, 1), stop)
    raise AssertionError("unreachable")


def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    # sync the directory too, so the rename survives a power loss (best
    # effort: not every platform can open a directory)
    try:
        fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        # some platforms refuse to fsync a directory; the file itself is synced
        return
    finally:
        os.close(fd)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _remove(path: Path) -> None:
    path.unlink(missing_ok=True)


def _read_range(path: Path, offset: int, length: int) -> bytes:
    with open(path, "rb") as handle:
        handle.seek(offset)
        return handle.read(length)


def _md5_hex(path: Path, offset: int, length: int) -> str:
    digest = hashlib.md5()  # noqa: S324 - etag comparison, not security
    with open(path, "rb") as handle:
        handle.seek(offset)
        remaining = length
        while remaining > 0:
            block = handle.read(min(8 * MIB, remaining))
            if not block:
                break
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def _is_md5_etag(etag: str) -> bool:
    return len(etag) == 32 and all(c in "0123456789abcdef" for c in etag.lower())


def _multipart_etag(parts: List[Dict[str, Any]]) -> str:
    """the etag s3 (and runpod, verified) gives an object assembled from
    parts: md5 of the parts' binary md5s, then "-<count>". "" when a part
    etag is not an md5."""
    digest = hashlib.md5()
    for part in parts:
        etag = _strip_etag(part["ETag"])
        if not _is_md5_etag(etag):
            return ""
        digest.update(bytes.fromhex(etag))
    return f"{digest.hexdigest()}-{len(parts)}"


def _run_pool(
    tasks: List[Callable[[], Any]],
    concurrency: int,
    stop: Optional[threading.Event] = None,
) -> List[Any]:
    """run tasks on a thread pool. the first failure cancels the rest.

    an interrupt (ctrl-c) also sets `stop`, before the pool waits for its
    running tasks, so workers checking it wind down instead of finishing
    every file in flight.
    """
    results = []
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [pool.submit(task) for task in tasks]
        try:
            for future in as_completed(futures):
                results.append(future.result())
        except BaseException as exc:
            if stop is not None and not isinstance(exc, Exception):
                stop.set()
            for future in futures:
                future.cancel()
            raise
    return results


def _is_download_leftover(path: Path) -> bool:
    """a partial download, its sidecar, or the sidecar's temp file."""
    return path.name.endswith(TRANSFER_SUFFIXES)


def _same_second_or_later(a: float, b: float) -> bool:
    """a >= b in whole seconds; s3 dates carry no finer precision."""
    return int(a) >= int(b)


def _check_remote_path(remote_path: Optional[str]) -> None:
    """refuse remote paths with '..' segments before anything is sent."""
    if remote_path and ".." in remote_path.replace("\\", "/").split("/"):
        raise ValueError(f"remote path {remote_path!r} must not contain '..'")


def _strip_etag(etag: Any) -> str:
    return str(etag or "").strip('"')


# ------------------------------------------------------------- transfers


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
        self._progress = progress
        self.state_dir = state_dir or _state_dir()
        self._lock = threading.Lock()
        # bounds requests in flight across all files: folder transfers run
        # files in parallel and each large file runs its parts in parallel
        self._slots = threading.BoundedSemaphore(self.concurrency)
        # transfer every file, even ones the skip rule calls unchanged
        self.force = force
        # set on ctrl-c so every worker winds down
        self._stop = threading.Event()
        self.summary = {"files_transferred": 0, "files_skipped": 0, "bytes_transferred": 0}

    # -- bookkeeping

    def _request(self, func: Callable[[], Any]) -> Any:
        if self._stop.is_set():
            raise _Stopped("interrupted")
        with self._slots:
            # checked again: an interrupt may land while waiting for the slot
            if self._stop.is_set():
                raise _Stopped("interrupted")
            return func()

    def _retry(self, func: Callable[[], Any]) -> Any:
        return _with_retries(func, stop=self._stop)

    def _pool(self, tasks: List[Callable[[], Any]]) -> List[Any]:
        return _run_pool(tasks, self.concurrency, self._stop)

    def _emit(self, event: str, path: str, nbytes: int = 0) -> None:
        field = _SUMMARY_FIELDS.get(event)
        if field is not None:
            with self._lock:
                self.summary[field] += nbytes if event == "advance" else 1
        if self._progress is not None:
            self._progress(event, path, nbytes)

    def _state_path(self, key: str, local: Path) -> Path:
        ident = f"{self.endpoint}|{self.bucket}|{key}|{local.resolve()}"
        return self.state_dir / (hashlib.sha256(ident.encode()).hexdigest() + ".json")

    # -- remote metadata

    def head(self, key: str, directory_ok: bool = False) -> Optional[Dict[str, Any]]:
        """object metadata, or None when there is no file at `key`.

        with `directory_ok`, a 400 also means "not a file": runpod answers
        HeadObject on a directory with 400 rather than 404.
        """
        try:
            return self._retry(
                lambda: self._request(
                    lambda: self.client.head_object(Bucket=self.bucket, Key=key)
                )
            )
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc) or (directory_ok and _error_code(exc)[1] == 400):
                return None
            raise

    def list_prefix(self, prefix: str) -> List[Dict[str, Any]]:
        objects: List[Dict[str, Any]] = []
        kwargs: Dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
        while True:
            try:
                page = self._retry(
                    lambda: self._request(lambda: self.client.list_objects_v2(**kwargs))
                )
            except Exception as exc:  # noqa: BLE001
                if _is_not_found(exc):
                    return objects
                raise
            objects.extend(page.get("Contents") or [])
            token = page.get("NextContinuationToken")
            if not page.get("IsTruncated") or not token:
                return objects
            kwargs["ContinuationToken"] = token

    def _is_folder(self, key: str) -> bool:
        """whether something is listed under `key/` (runpod lists even an
        empty folder's own marker)."""
        try:
            page = self._retry(
                lambda: self._request(
                    lambda: self.client.list_objects_v2(
                        Bucket=self.bucket, Prefix=f"{key}/", MaxKeys=1
                    )
                )
            )
        except Exception:  # noqa: BLE001 - unknown: report the original error
            return False
        return bool(page.get("Contents"))

    def _list_parts(self, key: str, upload_id: str) -> List[Dict[str, Any]]:
        parts: List[Dict[str, Any]] = []
        kwargs: Dict[str, Any] = {"Bucket": self.bucket, "Key": key, "UploadId": upload_id}
        while True:
            page = self._retry(
                lambda: self._request(lambda: self.client.list_parts(**kwargs))
            )
            parts.extend(page.get("Parts") or [])
            marker = page.get("NextPartNumberMarker")
            if not page.get("IsTruncated") or not marker:
                return parts
            kwargs["PartNumberMarker"] = marker

    # -- upload

    def upload(self, local: Path, remote_path: Optional[str]) -> Dict[str, int]:
        _check_remote_path(remote_path)
        if not local.exists():
            raise FileNotFoundError(str(local))
        if local.is_dir():
            self._upload_folder(local, remote_path)
        else:
            self._upload_single_file(local, remote_path)
        return self.summary

    def _upload_folder(self, local: Path, remote_path: Optional[str]) -> None:
        prefix = (local.resolve().name if remote_path is None else remote_path).strip("/")
        remote = {obj["Key"]: obj for obj in self.list_prefix(f"{prefix}/" if prefix else "")}
        jobs = []
        for path in sorted(p for p in local.rglob("*") if p.is_file()):
            key = _join_key(prefix, path.relative_to(local).as_posix())
            if not self._skip_upload(path, remote.get(key)):
                jobs.append((path, key))
        self._run_files([lambda p=p, k=k: self.upload_file(p, k) for p, k in jobs], jobs)

    def _skip_upload(self, path: Path, remote: Optional[Dict[str, Any]]) -> bool:
        """report a folder file that needs no upload: an interrupted
        download's partial file (not data), or an unchanged file."""
        leftover = _is_download_leftover(path)
        if not leftover and (self.force or not self._unchanged_remote(remote, path)):
            return False
        self._emit("skip", str(path), path.stat().st_size)
        return True

    def _upload_single_file(self, local: Path, remote_path: Optional[str]) -> None:
        key = _file_key(local, remote_path)
        remote = self._head_upload_target(key)
        if not self.force and self._unchanged_remote(remote, local):
            self._emit("skip", str(local), local.stat().st_size)
            return
        self.upload_file(local, key)

    def _head_upload_target(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            return self.head(key)
        except Exception as exc:  # noqa: BLE001
            # runpod answers a head on a directory with 400, but a head has no
            # body, so a 400 for any other reason looks the same: confirm
            if _error_code(exc)[1] == 400 and self._is_folder(key):
                raise NetworkVolumeTransferError(
                    f"{key!r} is a folder on the volume; use {key + '/'!r} to upload into it"
                ) from exc
            raise

    def _unchanged_remote(self, remote: Optional[Dict[str, Any]], local: Path) -> bool:
        if not remote:
            return False
        stat = local.stat()
        size = remote.get("Size", remote.get("ContentLength"))
        modified = remote.get("LastModified")
        if size is None or int(size) != stat.st_size or modified is None:
            return False
        return _same_second_or_later(modified.timestamp(), stat.st_mtime)

    def upload_file(self, local: Path, key: str) -> None:
        stat = local.stat()
        label = str(local)
        self._emit("start", label, stat.st_size)
        if stat.st_size <= SINGLE_PUT_MAX:
            self._single_put(local, key, stat)
        else:
            self._multipart_upload(_Upload(local, key, stat, part_size_for(stat.st_size)))
        self._emit("done", label)

    def _single_put(self, local: Path, key: str, stat: os.stat_result) -> None:
        self._retry(
            lambda: self._request(
                lambda: self.client.put_object(Bucket=self.bucket, Key=key, Body=local.read_bytes())
            )
        )
        self._delete_if_changed(local, key, stat)
        self._emit("advance", str(local), stat.st_size)

    def _delete_if_changed(self, local: Path, key: str, stat: os.stat_result) -> None:
        """a retry rereads the file; if it changed meanwhile, the object may
        hold either version and is newer than the local edit, so a re-run
        would skip it. remove it so the re-run sends the file."""
        try:
            now = local.stat()
        except OSError as exc:
            # gone or unreadable: the object is what was read, keep it
            raise NetworkVolumeTransferError(f"{local} is gone after its upload: {exc}") from exc
        if _same_version(stat, now):
            return
        try:
            # not via self._retry: this cleanup runs even after ctrl-c
            _with_retries(lambda: self.client.delete_object(Bucket=self.bucket, Key=key))
        except Exception as exc:  # noqa: BLE001
            raise NetworkVolumeTransferError(
                f"{local} changed during upload and the remote copy could not be "
                f"removed ({exc}); re-run with --force"
            ) from exc
        raise NetworkVolumeTransferError(f"{local} changed during upload", resumable=True)

    def _multipart_upload(self, up: "_Upload") -> None:
        up.state_path = self._state_path(up.key, up.local)
        if not self._resume_upload(up):
            self._create_upload(up)
        reused = sum(up.part_length(n) for n in up.parts)
        if reused:
            self._emit("reuse", up.label, reused)
        self._send_missing_parts(up)
        # the parts were read over the whole upload; if the file moved under
        # them, they may mix two versions of it
        if not _same_version(up.stat, up.local.stat()):
            self._abort(up.key, up.upload_id)
            _remove(up.state_path)
            raise NetworkVolumeTransferError(f"{up.local} changed during upload", resumable=True)
        self._complete_upload(up)
        _remove(up.state_path)

    def _resume_upload(self, up: "_Upload") -> bool:
        """pick up the saved upload for this file with its verified parts.
        false when there is none to resume."""
        state = _read_json(up.state_path)
        if not state or not state.get("upload_id"):
            return False
        if not up.matches(state):
            # the local file changed since the upload began: its parts are stale
            self._abort(up.key, state["upload_id"])
            _remove(up.state_path)
            return False
        try:
            up.parts = self._verified_parts(up, state["upload_id"])
        except LookupError:
            # the server no longer has this upload: start a new one
            _remove(up.state_path)
            return False
        up.upload_id = state["upload_id"]
        return True

    def _verified_parts(self, up: "_Upload", upload_id: str) -> Dict[int, str]:
        """parts of the saved upload that match the local file."""
        try:
            listed = self._list_parts(up.key, upload_id)
        except Exception as exc:  # noqa: BLE001
            code, _ = _error_code(exc)
            if code == "NoSuchUpload" or _is_not_found(exc):
                raise LookupError(upload_id) from exc
            raise
        return {
            int(part["PartNumber"]): part.get("ETag")
            for part in listed
            if up.part_matches(part)
        }

    def _create_upload(self, up: "_Upload") -> None:
        created = self._retry(
            lambda: self._request(
                lambda: self.client.create_multipart_upload(Bucket=self.bucket, Key=up.key)
            )
        )
        up.upload_id = created["UploadId"]
        up.parts = {}
        try:
            _write_json_atomic(up.state_path, up.state(self.bucket))
        except OSError:
            # without saved state no re-run can find this upload again
            self._abort(up.key, up.upload_id)
            raise

    def _send_missing_parts(self, up: "_Upload") -> None:
        todo = [n for n in range(1, up.count + 1) if n not in up.parts]
        for number, etag in self._pool([lambda n=n: self._send_part(up, n) for n in todo]):
            up.parts[number] = etag

    def _send_part(self, up: "_Upload", number: int) -> Tuple[int, str]:
        offset = (number - 1) * up.part_size
        length = up.part_length(number)
        response = self._retry(
            lambda: self._request(
                lambda: self.client.upload_part(
                    Bucket=self.bucket,
                    Key=up.key,
                    UploadId=up.upload_id,
                    PartNumber=number,
                    Body=_read_range(up.local, offset, length),
                )
            )
        )
        self._emit("advance", up.label, length)
        return number, response["ETag"]

    def _complete_upload(self, up: "_Upload") -> None:
        parts = [{"PartNumber": n, "ETag": up.parts[n]} for n in sorted(up.parts)]
        try:
            self._retry(
                lambda: self._request(
                    lambda: self.client.complete_multipart_upload(
                        Bucket=self.bucket,
                        Key=up.key,
                        UploadId=up.upload_id,
                        MultipartUpload={"Parts": parts},
                    )
                )
            )
        except Exception as exc:  # noqa: BLE001 - re-raised unless it completed
            if not self._completed_anyway(exc, up.key, up.stat.st_size, _multipart_etag(parts)):
                raise

    def _completed_anyway(self, exc: Exception, key: str, size: int, want_etag: str) -> bool:
        """whether a failed complete took effect anyway.

        assembling a large file can outlive the proxy (524) while it still
        finishes server-side, and a retry then finds the upload gone. only
        those ambiguous failures qualify, and only when the object at the key
        is this upload's: its size matches and its etag equals the multipart
        etag of our parts.
        """
        if not (_is_retryable(exc) or _is_not_found(exc)):
            return False
        head = self.head(key)
        if head is None or int(head.get("ContentLength", -1)) != size:
            return False
        # without our multipart etag the object's identity cannot be confirmed
        return bool(want_etag) and _strip_etag(head.get("ETag")) == want_etag

    def _abort(self, key: str, upload_id: str) -> None:
        """discard an upload that can no longer be resumed (best effort)."""
        try:
            self._request(
                lambda: self.client.abort_multipart_upload(
                    Bucket=self.bucket, Key=key, UploadId=upload_id
                )
            )
        except Exception:  # noqa: BLE001
            # a failed abort only leaves orphaned parts behind
            return

    def _run_files(self, tasks: List[Callable[[], Any]], jobs: List[Tuple[Any, Any]]) -> None:
        """run per-file tasks. every file is attempted; failures are reported
        together, resumable only when every one of them is."""
        failures = []
        resumable = []

        def guarded(index: int) -> None:
            try:
                tasks[index]()
            except Exception as exc:  # noqa: BLE001 - collected and re-raised
                with self._lock:
                    failures.append(f"{jobs[index][0]}: {exc}")
                    resumable.append(is_resumable(exc))

        self._pool([lambda i=i: guarded(i) for i in range(len(tasks))])
        if failures:
            raise NetworkVolumeTransferError(
                f"{len(failures)} file(s) failed:\n  " + "\n  ".join(sorted(failures)),
                resumable=all(resumable),
            )

    # -- download

    def download(self, remote_path: str, local_path: Any = None) -> Dict[str, int]:
        """`local_path` is a str or Path; a str ending in "/" is a folder to
        download into (a Path cannot carry the slash)."""
        _check_remote_path(remote_path)
        key = remote_path.lstrip("/")
        head = None if key == "" or key.endswith("/") else self.head(key, directory_ok=True)
        if head is not None:
            self._download_object(key, head, local_path)
        else:
            self._download_prefix(key.rstrip("/"), remote_path, local_path)
        return self.summary

    def _download_object(self, key: str, head: Dict[str, Any], local_path: Any) -> None:
        self.download_file(
            key,
            int(head["ContentLength"]),
            _strip_etag(head.get("ETag")),
            _object_dest(key, local_path),
            head.get("LastModified"),
        )

    def _download_prefix(self, prefix: str, remote_path: str, local_path: Any) -> None:
        list_prefix = f"{prefix}/" if prefix else ""
        objects = self._listed_files(list_prefix)
        # a typo must not look like an empty folder; the whole volume may be empty
        if not objects and prefix:
            raise FileNotFoundError(f"no such file or directory on the volume: {remote_path}")
        root = Path(local_path) if local_path is not None else Path(
            PurePosixPath(prefix).name or self.bucket
        )
        jobs = [(_safe_target(root, obj["Key"][len(list_prefix):]), obj) for obj in objects]
        _check_local_names([dest for dest, _ in jobs], root)
        self._run_files(
            [
                lambda d=d, o=o: self.download_file(
                    o["Key"], int(o["Size"]), _strip_etag(o.get("ETag")), d, o.get("LastModified")
                )
                for d, o in jobs
            ],
            jobs,
        )

    def _listed_files(self, list_prefix: str) -> List[Dict[str, Any]]:
        """the files under a prefix, without folder markers and in-progress
        multipart parts."""
        return [
            obj
            for obj in self.list_prefix(list_prefix)
            if not obj["Key"].endswith("/")
            and UPLOADS_DIR not in PurePosixPath(obj["Key"]).parts
        ]

    def download_file(
        self, key: str, size: int, etag: str, dest: Path, modified: Any = None
    ) -> None:
        job = _Download(key, size, etag, dest, modified)
        if self._unchanged_local(job):
            self._emit("skip", job.label, size)
            return
        self._emit("start", job.label, size)
        dest.parent.mkdir(parents=True, exist_ok=True)
        job.completed_chunks = job.load_or_init()
        reused = sum(job.chunk_length(i) for i in job.completed_chunks)
        if reused:
            self._emit("reuse", job.label, reused)
        todo = [i for i in range(job.count) if i not in job.completed_chunks]
        self._pool([lambda i=i: self._fetch_chunk(job, i) for i in todo])
        self._finish_download(job)

    def _unchanged_local(self, job: "_Download") -> bool:
        """downloads stamp the remote time, so an unchanged file is the same
        size and not older than the remote copy."""
        if self.force or not job.dest.is_file():
            return False
        stat = job.dest.stat()
        if stat.st_size != job.size:
            return False
        return job.modified is None or _same_second_or_later(
            stat.st_mtime, job.modified.timestamp()
        )

    def _fetch_chunk(self, job: "_Download", index: int) -> None:
        start = index * DOWNLOAD_CHUNK_SIZE
        length = job.chunk_length(index)
        data = self._retry(lambda: self._request(lambda: self._get_range(job, start, length)))
        _write_at(job.tmp, start, data)
        with self._lock:
            job.completed_chunks.add(index)
            job.save_state()
        self._emit("advance", job.label, length)

    def _get_range(self, job: "_Download", start: int, length: int) -> bytes:
        end = start + length - 1
        response = self.client.get_object(
            Bucket=self.bucket, Key=job.key, Range=f"bytes={start}-{end}"
        )
        got = _strip_etag(response.get("ETag"))
        if job.etag and got and got != job.etag:
            raise NetworkVolumeTransferError(
                f"{job.key} changed on the volume during download; "
                "run the command again to restart it",
                resumable=True,
            )
        data = response["Body"].read()
        if len(data) != length:
            raise _Retryable(f"short read for {job.key} bytes {start}-{end}")
        return data

    def _finish_download(self, job: "_Download") -> None:
        if job.tmp.stat().st_size != job.size:
            raise NetworkVolumeTransferError(
                f"{job.dest}: size mismatch after download", resumable=True
            )
        os.replace(job.tmp, job.dest)
        if job.modified is not None:
            # stamp the remote time so uploading the file back counts as unchanged
            os.utime(job.dest, (time.time(), job.modified.timestamp()))
        _remove(job.sidecar)
        self._emit("done", job.label)


class _Upload:
    """one multipart upload of a local file, pinned to its size and mtime."""

    def __init__(self, local: Path, key: str, stat: os.stat_result, part_size: int):
        self.local = local
        self.key = key
        self.stat = stat
        self.part_size = part_size
        self.count = math.ceil(stat.st_size / part_size)
        self.label = str(local)
        self.state_path = Path()
        self.upload_id = ""
        self.parts: Dict[int, str] = {}  # part number -> etag

    def part_length(self, number: int) -> int:
        return min(self.part_size, self.stat.st_size - (number - 1) * self.part_size)

    def matches(self, state: Dict[str, Any]) -> bool:
        """whether a saved state belongs to this version of the file."""
        return (
            state.get("file_size") == self.stat.st_size
            and state.get("mtime_ns") == self.stat.st_mtime_ns
            and state.get("part_size") == self.part_size
        )

    def part_matches(self, part: Dict[str, Any]) -> bool:
        """whether a listed part holds this file's bytes. a part whose
        content cannot be verified is uploaded again."""
        number = int(part["PartNumber"])
        expected = self.part_length(number)
        if expected <= 0 or int(part.get("Size", -1)) != expected:
            return False
        etag = _strip_etag(part.get("ETag"))
        offset = (number - 1) * self.part_size
        return _is_md5_etag(etag) and _md5_hex(self.local, offset, expected) == etag.lower()

    def state(self, bucket: str) -> Dict[str, Any]:
        return {
            "upload_id": self.upload_id,
            "part_size": self.part_size,
            "file_size": self.stat.st_size,
            "mtime_ns": self.stat.st_mtime_ns,
            "bucket": bucket,
            "key": self.key,
        }


class _Download:
    """one file download into `<dest>.rpdownload`, with its finished chunks
    recorded in a sidecar json."""

    def __init__(self, key: str, size: int, etag: str, dest: Path, modified: Any):
        self.key = key
        self.size = size
        self.etag = etag
        self.dest = dest
        self.modified = modified
        self.label = str(dest)
        self.tmp = dest.with_name(dest.name + PARTIAL_SUFFIX)
        self.sidecar = dest.with_name(dest.name + SIDECAR_SUFFIX)
        self.count = math.ceil(size / DOWNLOAD_CHUNK_SIZE)
        self.completed_chunks: set = set()

    def chunk_length(self, index: int) -> int:
        return min(DOWNLOAD_CHUNK_SIZE, self.size - index * DOWNLOAD_CHUNK_SIZE)

    def load_or_init(self) -> set:
        """the chunks an earlier run finished, or a fresh partial file."""
        state = _read_json(self.sidecar)
        if self._resumes(state):
            return {int(i) for i in state.get("done", []) if 0 <= int(i) < self.count}
        with open(self.tmp, "wb") as handle:
            handle.truncate(self.size)
        self.save_state()
        return set()

    def _resumes(self, state: Optional[Dict[str, Any]]) -> bool:
        # the etag is only compared for equality. a multipart object's etag
        # is "<md5>-<parts>", never the md5 of the file.
        return bool(
            state
            and state.get("key") == self.key
            and state.get("size") == self.size
            and state.get("etag") == self.etag
            and state.get("chunk_size") == DOWNLOAD_CHUNK_SIZE
            and self.tmp.is_file()
            and self.tmp.stat().st_size == self.size
        )

    def save_state(self) -> None:
        _write_json_atomic(
            self.sidecar,
            {
                "key": self.key,
                "size": self.size,
                "etag": self.etag,
                "chunk_size": DOWNLOAD_CHUNK_SIZE,
                "done": sorted(self.completed_chunks),
            },
        )


def _same_version(a: os.stat_result, b: os.stat_result) -> bool:
    """whether two stats of a file show the same size and mtime."""
    return a.st_size == b.st_size and a.st_mtime_ns == b.st_mtime_ns


def _join_key(prefix: str, rel: str) -> str:
    return f"{prefix}/{rel}" if prefix else rel


def _file_key(local: Path, remote_path: Optional[str]) -> str:
    """the key for a single-file upload. it defaults to the local name, and
    a remote path ending in "/" keeps the local name inside that folder."""
    if remote_path is None:
        return local.name
    key = remote_path.lstrip("/")
    return key + local.name if remote_path.endswith("/") else key


def _object_dest(key: str, local_path: Any) -> Path:
    """the local file for a single-object download. a trailing slash names
    a folder to download into, as on upload."""
    name = PurePosixPath(key).name
    dest = Path(local_path) if local_path is not None else Path(name)
    if dest.is_dir() or str(local_path or "").endswith(("/", os.sep)):
        return dest / name
    return dest


def _write_at(path: Path, offset: int, data: bytes) -> None:
    """write and sync data at offset, so it is on disk before the sidecar
    claims it."""
    with open(path, "r+b") as handle:
        handle.seek(offset)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _fold(name: str) -> str:
    """the key under which two spellings a filesystem may treat as one name
    collide: nfc first (apfs compares names normalization-insensitively),
    then unicode case folding."""
    return unicodedata.normalize("NFC", name).casefold()


def _check_local_names(targets: List[Path], root: Path) -> None:
    """refuse targets that would share a path on disk: two targets that are
    one name on a case-insensitive or normalizing filesystem ("Model.bin" and
    "model.bin"), a file whose name is also a folder another file lives in
    ("a" beside "A/b"), or a file whose partial, sidecar, or sidecar temp
    name is also a target or a directory a target lives in ("x" beside
    "x.rpdownload" or "x.rpdownload/y"). that can refuse a listing a
    case-sensitive filesystem would take, which is the safe direction."""
    files, dirs = _index_names(targets, root)
    _check_file_folder_clash(files, dirs)
    _check_transfer_names(targets, root, files, dirs)


def _index_names(targets: List[Path], root: Path) -> Tuple[Dict[str, Path], Dict[str, Path]]:
    """targets and the folders they live in, keyed by folded name."""
    files: Dict[str, Path] = {}
    dirs: Dict[str, Path] = {}
    for target in targets:
        rel = target.relative_to(root)
        key = _fold(str(rel))
        if key in files:
            raise NetworkVolumeTransferError(
                f"cannot download both {files[key]} and {target}: they would be one "
                "file on a case-insensitive or normalizing filesystem"
            )
        files[key] = target
        dirs.update({_fold(str(parent)): target for parent in rel.parents if str(parent) != "."})
    return files, dirs


def _check_file_folder_clash(files: Dict[str, Path], dirs: Dict[str, Path]) -> None:
    clash = next((key for key in files if key in dirs), None)
    if clash is not None:
        raise NetworkVolumeTransferError(
            f"cannot download both {files[clash]} and {dirs[clash]}: "
            "the first would have to be a file and a folder"
        )


def _check_transfer_names(
    targets: List[Path], root: Path, files: Dict[str, Path], dirs: Dict[str, Path]
) -> None:
    """refuse a target whose download working files would land on another
    target or a folder one lives in."""
    for target in targets:
        rel = target.relative_to(root)
        for suffix in TRANSFER_SUFFIXES:
            twin = _fold(str(rel.with_name(rel.name + suffix)))
            other = files.get(twin) or dirs.get(twin)
            if other is not None:
                raise NetworkVolumeTransferError(
                    f"cannot download both {target} and {other}: "
                    f"{rel.name + suffix} is used for the first's partial download"
                )


def _safe_target(root: Path, rel: str) -> Path:
    """local path for a remote relative key, refusing anything outside root."""
    parts = PurePosixPath(rel).parts
    if (
        not rel
        or rel.startswith("/")
        or "\\" in rel
        # PurePosixPath drops "." and empty segments; "a/./b" would alias "a/b"
        or any(seg in ("..", ".", "") for seg in rel.split("/"))
        or any(part in ("..", "") for part in parts)
        or (parts and ":" in parts[0])
    ):
        raise NetworkVolumeTransferError(f"refusing unsafe remote path: {rel!r}")
    target = root.joinpath(*parts)
    resolved_root = root.resolve()
    if resolved_root not in target.resolve().parents:
        raise NetworkVolumeTransferError(f"refusing unsafe remote path: {rel!r}")
    return target


# ---------------------------------------------------------------- client


def _resolve_volume(reference: str) -> Tuple[str, str]:
    """the id and datacenter of the network volume with this id or name."""
    from runpod.api.rest import run_rest_paginated_request_async  # pylint: disable=import-outside-toplevel
    from runpod.apps.context import block  # pylint: disable=import-outside-toplevel
    from runpod.apps.utils.lookup import find_by_id_or_name  # pylint: disable=import-outside-toplevel

    volumes = block(run_rest_paginated_request_async("/v2/network-volumes", "networkVolumes"))
    record = find_by_id_or_name(
        volumes, reference, noun="network volumes", error=NetworkVolumeTransferError
    )
    if record is None:
        raise NetworkVolumeTransferError(f"network volume {reference!r} not found")
    datacenter = record.get("dataCenter") or record.get("dataCenterId")
    if not datacenter:
        raise NetworkVolumeTransferError(
            f"could not find the datacenter of network volume {reference!r}"
        )
    return record["id"], str(datacenter)


def _check_endpoint_resolves(endpoint: str) -> None:
    """fail fast on a datacenter whose s3 endpoint does not resolve, before
    any transfer starts. once a transfer is running, a failed lookup is a
    network drop and is retried like any other. behind an https proxy (which
    botocore honours from the environment) the proxy resolves the name, so
    the local lookup is skipped."""
    from urllib.parse import urlparse  # pylint: disable=import-outside-toplevel
    from urllib.request import getproxies, proxy_bypass  # pylint: disable=import-outside-toplevel

    parsed = urlparse(endpoint)
    host = parsed.hostname
    if not host:
        raise NetworkVolumeTransferError(f"invalid s3 endpoint {endpoint!r}")
    if getproxies().get(parsed.scheme or "https") and not proxy_bypass(host):
        return
    try:
        socket.getaddrinfo(host, 443)
    except socket.gaierror as exc:
        raise NetworkVolumeTransferError(
            f"the s3 endpoint {host} does not resolve: the volume's datacenter "
            "may not support the s3 api, or check your network connection"
        ) from exc


def _credentials(access_key: Optional[str], secret_key: Optional[str]) -> Tuple[str, str]:
    access_key = access_key or os.environ.get(ACCESS_KEY_ENV)
    secret_key = secret_key or os.environ.get(SECRET_KEY_ENV)
    if not access_key or not secret_key:
        raise NetworkVolumeTransferError(
            f"s3 api keys required: set {ACCESS_KEY_ENV} and {SECRET_KEY_ENV} "
            f"(create keys at {S3_KEYS_URL})"
        )
    return access_key, secret_key


def _client_config(concurrency: int) -> Any:
    from botocore.config import Config  # pylint: disable=import-outside-toplevel

    return Config(
        s3={"addressing_style": "path"},
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        # _with_retries owns retries; botocore's would multiply them and sleep
        # while holding a concurrency slot
        retries={"total_max_attempts": 1, "mode": "standard"},
        connect_timeout=60,
        # a socket read silent this long is a stalled connection (runpod's
        # proxy answers a slow request with 524 after about 100s)
        read_timeout=300,
        # every call holds one of `concurrency` slots; headroom for
        # connections still being returned to the pool
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

    access_key, secret_key = _credentials(access_key, secret_key)
    volume_id, datacenter = _resolve_volume(volume)
    endpoint = endpoint_url or f"https://s3api-{datacenter.lower()}.runpod.io/"
    if not endpoint_url:
        # an explicit endpoint (a local stand-in, a custom route) is the
        # caller's; only the derived datacenter endpoint is checked
        _check_endpoint_resolves(endpoint)
    concurrency = max(1, int(concurrency))
    client = boto3.session.Session().client(
        "s3",
        region_name=datacenter,
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=_client_config(concurrency),
    )
    return _Transfer(client, volume_id, endpoint, concurrency, progress, force=force)
