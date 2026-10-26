"""resumable file and folder transfers to and from network volumes.

transfers go through the runpod s3-compatible api. the bucket is the
network volume id and the endpoint is per datacenter.

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
    return path.name.endswith((PARTIAL_SUFFIX, SIDECAR_SUFFIX, SIDECAR_SUFFIX + ".tmp"))


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


class _Transfer:
    """one bucket on one endpoint. the public functions build this."""

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
            return func()

    def _retry(self, func: Callable[[], Any]) -> Any:
        return _with_retries(func, stop=self._stop)

    def _pool(self, tasks: List[Callable[[], Any]]) -> List[Any]:
        return _run_pool(tasks, self.concurrency, self._stop)

    def _emit(self, event: str, path: str, nbytes: int = 0) -> None:
        with self._lock:
            if event == "advance":
                self.summary["bytes_transferred"] += nbytes
            elif event == "done":
                self.summary["files_transferred"] += 1
            elif event == "skip":
                self.summary["files_skipped"] += 1
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

    def upload_file(self, local: Path, key: str) -> None:
        stat = local.stat()
        size = stat.st_size
        label = str(local)
        self._emit("start", label, size)
        if size <= SINGLE_PUT_MAX:
            self._retry(
                lambda: self._request(
                    lambda: self.client.put_object(
                        Bucket=self.bucket, Key=key, Body=local.read_bytes()
                    )
                )
            )
            # a retry rereads the file; if it changed meanwhile, the object
            # may hold either version and is newer than the local edit, so a
            # re-run would skip it. remove it so the re-run sends the file.
            try:
                now = local.stat()
            except OSError as exc:
                # gone or unreadable: the object is what was read, keep it
                raise NetworkVolumeTransferError(f"{local} is gone after its upload: {exc}") from exc
            if now.st_size != size or now.st_mtime_ns != stat.st_mtime_ns:
                try:
                    # not via self._retry: this cleanup runs even after ctrl-c
                    _with_retries(lambda: self.client.delete_object(Bucket=self.bucket, Key=key))
                except Exception as exc:  # noqa: BLE001
                    raise NetworkVolumeTransferError(
                        f"{local} changed during upload and the remote copy could not be "
                        f"removed ({exc}); re-run with --force"
                    ) from exc
                raise NetworkVolumeTransferError(
                    f"{local} changed during upload", resumable=True
                )
            self._emit("advance", label, size)
        else:
            self._multipart_upload(local, key, size, stat.st_mtime_ns, label)
        self._emit("done", label)

    def _resume_parts(
        self, local: Path, key: str, state: Dict[str, Any], size: int, part_size: int
    ) -> Dict[int, str]:
        """parts of the saved upload that match the local file."""
        upload_id = state["upload_id"]
        try:
            listed = self._list_parts(key, upload_id)
        except Exception as exc:  # noqa: BLE001
            code, _ = _error_code(exc)
            if code == "NoSuchUpload" or _is_not_found(exc):
                raise LookupError(upload_id) from exc
            raise
        done: Dict[int, str] = {}
        for part in listed:
            number = int(part["PartNumber"])
            offset = (number - 1) * part_size
            expected = min(part_size, size - offset)
            if expected <= 0 or int(part.get("Size", -1)) != expected:
                continue
            # a part whose content cannot be verified is uploaded again
            etag = _strip_etag(part.get("ETag"))
            if not _is_md5_etag(etag) or _md5_hex(local, offset, expected) != etag.lower():
                continue
            done[number] = part.get("ETag")
        return done

    def _multipart_upload(
        self, local: Path, key: str, size: int, mtime_ns: int, label: str
    ) -> None:
        part_size = part_size_for(size)
        count = math.ceil(size / part_size)
        state_path = self._state_path(key, local)
        state = _read_json(state_path)
        upload_id: Optional[str] = None
        done: Dict[int, str] = {}

        if state and state.get("upload_id"):
            same = (
                state.get("file_size") == size
                and state.get("mtime_ns") == mtime_ns
                and state.get("part_size") == part_size
            )
            if same:
                try:
                    done = self._resume_parts(local, key, state, size, part_size)
                    upload_id = state["upload_id"]
                except LookupError:
                    # the server no longer has this upload: start a new one
                    upload_id = None
            else:
                self._abort(key, state["upload_id"])
            if upload_id is None:
                _remove(state_path)

        if upload_id is None:
            created = self._retry(
                lambda: self._request(
                    lambda: self.client.create_multipart_upload(Bucket=self.bucket, Key=key)
                )
            )
            upload_id = created["UploadId"]
            try:
                _write_json_atomic(
                    state_path,
                    {
                        "upload_id": upload_id,
                        "part_size": part_size,
                        "file_size": size,
                        "mtime_ns": mtime_ns,
                        "bucket": self.bucket,
                        "key": key,
                    },
                )
            except OSError:
                # without saved state no re-run can find this upload again
                self._abort(key, upload_id)
                raise

        already = sum(min(part_size, size - (n - 1) * part_size) for n in done)
        if already:
            self._emit("reuse", label, already)

        def upload_part(number: int) -> Tuple[int, str]:
            offset = (number - 1) * part_size
            length = min(part_size, size - offset)

            def send():
                body = _read_range(local, offset, length)
                return self.client.upload_part(
                    Bucket=self.bucket,
                    Key=key,
                    UploadId=upload_id,
                    PartNumber=number,
                    Body=body,
                )

            response = self._retry(lambda: self._request(send))
            self._emit("advance", label, length)
            return number, response["ETag"]

        todo = [n for n in range(1, count + 1) if n not in done]
        for number, etag in self._pool([lambda n=n: upload_part(n) for n in todo]):
            done[number] = etag

        # the parts were read over the whole upload; if the file moved under
        # them, they may mix two versions of it
        now = local.stat()
        if now.st_size != size or now.st_mtime_ns != mtime_ns:
            self._abort(key, upload_id)
            _remove(state_path)
            raise NetworkVolumeTransferError(f"{local} changed during upload", resumable=True)

        parts = [{"PartNumber": n, "ETag": done[n]} for n in sorted(done)]
        try:
            self._retry(
                lambda: self._request(
                    lambda: self.client.complete_multipart_upload(
                        Bucket=self.bucket,
                        Key=key,
                        UploadId=upload_id,
                        MultipartUpload={"Parts": parts},
                    )
                )
            )
        except Exception as exc:  # noqa: BLE001 - re-raised unless it completed
            if not self._completed_anyway(exc, key, size, _multipart_etag(parts)):
                raise
        _remove(state_path)

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

    def _unchanged_remote(self, remote: Optional[Dict[str, Any]], local: Path) -> bool:
        if not remote:
            return False
        stat = local.stat()
        size = remote.get("Size", remote.get("ContentLength"))
        modified = remote.get("LastModified")
        if size is None or int(size) != stat.st_size or modified is None:
            return False
        return _same_second_or_later(modified.timestamp(), stat.st_mtime)

    def upload(self, local: Path, remote_path: Optional[str]) -> Dict[str, int]:
        _check_remote_path(remote_path)
        if not local.exists():
            raise FileNotFoundError(str(local))
        if local.is_dir():
            prefix = (local.resolve().name if remote_path is None else remote_path).strip("/")
            remote = {
                obj["Key"]: obj
                for obj in self.list_prefix(f"{prefix}/" if prefix else "")
            }
            jobs = []
            for path in sorted(p for p in local.rglob("*") if p.is_file()):
                if _is_download_leftover(path):
                    # an interrupted download's partial file is not data
                    self._emit("skip", str(path), path.stat().st_size)
                    continue
                rel = path.relative_to(local).as_posix()
                key = f"{prefix}/{rel}" if prefix else rel
                if not self.force and self._unchanged_remote(remote.get(key), path):
                    self._emit("skip", str(path), path.stat().st_size)
                    continue
                jobs.append((path, key))
            self._run_files([lambda p=p, k=k: self.upload_file(p, k) for p, k in jobs], jobs)
            return self.summary

        if remote_path is None:
            key = local.name
        elif remote_path.endswith("/"):
            key = remote_path.lstrip("/") + local.name
        else:
            key = remote_path.lstrip("/")
        try:
            remote = self.head(key)
        except Exception as exc:  # noqa: BLE001
            # runpod answers a head on a directory with 400, but a head has no
            # body, so a 400 for any other reason looks the same: confirm
            if _error_code(exc)[1] == 400 and self._is_folder(key):
                raise NetworkVolumeTransferError(
                    f"{key!r} is a folder on the volume; use {key + '/'!r} to upload into it"
                ) from exc
            raise
        if not self.force and self._unchanged_remote(remote, local):
            self._emit("skip", str(local), local.stat().st_size)
            return self.summary
        self.upload_file(local, key)
        return self.summary

    def _run_files(self, tasks: List[Callable[[], Any]], jobs: List[Tuple[Any, Any]]) -> None:
        """run per-file tasks. every file is attempted; failures are reported together."""
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
                resumable=any(resumable),
            )

    # -- download

    def download_file(
        self, key: str, size: int, etag: str, dest: Path, modified: Any = None
    ) -> None:
        label = str(dest)
        # downloads stamp the remote time, so an unchanged file is the same
        # size and not older than the remote copy
        if (
            not self.force
            and dest.is_file()
            and dest.stat().st_size == size
            and (
                modified is None
                or _same_second_or_later(dest.stat().st_mtime, modified.timestamp())
            )
        ):
            self._emit("skip", label, size)
            return
        self._emit("start", label, size)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + PARTIAL_SUFFIX)
        sidecar = dest.with_name(dest.name + SIDECAR_SUFFIX)
        state = _read_json(sidecar)
        done: set = set()
        # the etag is only compared for equality. a multipart object's etag
        # is "<md5>-<parts>", never the md5 of the file.
        if (
            state
            and state.get("key") == key
            and state.get("size") == size
            and state.get("etag") == etag
            and state.get("chunk_size") == DOWNLOAD_CHUNK_SIZE
            and tmp.is_file()
            and tmp.stat().st_size == size
        ):
            done = {int(i) for i in state.get("done", [])}
        else:
            with open(tmp, "wb") as handle:
                handle.truncate(size)
            _write_json_atomic(
                sidecar,
                {
                    "key": key,
                    "size": size,
                    "etag": etag,
                    "chunk_size": DOWNLOAD_CHUNK_SIZE,
                    "done": [],
                },
            )

        count = math.ceil(size / DOWNLOAD_CHUNK_SIZE)
        already = sum(
            min(DOWNLOAD_CHUNK_SIZE, size - i * DOWNLOAD_CHUNK_SIZE) for i in done if i < count
        )
        if already:
            self._emit("reuse", label, already)

        def fetch(index: int) -> None:
            start = index * DOWNLOAD_CHUNK_SIZE
            end = min(start + DOWNLOAD_CHUNK_SIZE, size) - 1

            def get():
                response = self.client.get_object(
                    Bucket=self.bucket, Key=key, Range=f"bytes={start}-{end}"
                )
                got = _strip_etag(response.get("ETag"))
                if etag and got and got != etag:
                    raise NetworkVolumeTransferError(
                        f"{key} changed on the volume during download; "
                        "run the command again to restart it",
                        resumable=True,
                    )
                data = response["Body"].read()
                if len(data) != end - start + 1:
                    raise _Retryable(f"short read for {key} bytes {start}-{end}")
                return data

            data = self._retry(lambda: self._request(get))
            with open(tmp, "r+b") as handle:
                handle.seek(start)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            with self._lock:
                done.add(index)
                _write_json_atomic(
                    sidecar,
                    {
                        "key": key,
                        "size": size,
                        "etag": etag,
                        "chunk_size": DOWNLOAD_CHUNK_SIZE,
                        "done": sorted(done),
                    },
                )
            self._emit("advance", label, end - start + 1)

        todo = [i for i in range(count) if i not in done]
        self._pool([lambda i=i: fetch(i) for i in todo])

        if tmp.stat().st_size != size:
            raise NetworkVolumeTransferError(
                f"{dest}: size mismatch after download", resumable=True
            )
        os.replace(tmp, dest)
        if modified is not None:
            # stamp the remote time so uploading the file back counts as unchanged
            os.utime(dest, (time.time(), modified.timestamp()))
        _remove(sidecar)
        self._emit("done", label)

    def download(self, remote_path: str, local_path: Any = None) -> Dict[str, int]:
        """`local_path` is a str or Path; a str ending in "/" is a folder to
        download into (a Path cannot carry the slash)."""
        _check_remote_path(remote_path)
        key = remote_path.lstrip("/")
        head = (
            None if key == "" or key.endswith("/") else self.head(key, directory_ok=True)
        )
        if head is not None:
            name = PurePosixPath(key).name
            dest = Path(local_path) if local_path is not None else Path(name)
            # a trailing slash names a folder to download into, as on upload
            if dest.is_dir() or str(local_path or "").endswith(("/", os.sep)):
                dest = dest / name
            self.download_file(
                key,
                int(head["ContentLength"]),
                _strip_etag(head.get("ETag")),
                dest,
                head.get("LastModified"),
            )
            return self.summary

        prefix = key.rstrip("/")
        list_prefix = f"{prefix}/" if prefix else ""
        objects = [
            obj
            for obj in self.list_prefix(list_prefix)
            if not obj["Key"].endswith("/")
            and UPLOADS_DIR not in PurePosixPath(obj["Key"]).parts
        ]
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
        return self.summary


def _fold(name: str) -> str:
    """the key under which two spellings a filesystem may treat as one name
    collide: nfc first (apfs compares names normalization-insensitively),
    then unicode case folding."""
    return unicodedata.normalize("NFC", name).casefold()


def _check_local_names(targets: List[Path], root: Path) -> None:
    """refuse targets that would share a path on disk: two targets that are
    one name on a case-insensitive or normalizing filesystem ("Model.bin" and
    "model.bin"), a file whose name is also a folder another file lives in
    ("a" beside "A/b"), or a file whose partial or sidecar name is also a target or
    a directory a target lives in ("x" beside "x.rpdownload" or
    "x.rpdownload/y"). that can refuse a listing a case-sensitive filesystem
    would take, which is the safe direction."""
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
        for parent in rel.parents:
            if str(parent) != ".":
                dirs[_fold(str(parent))] = target
    for key, target in files.items():
        if key in dirs:
            raise NetworkVolumeTransferError(
                f"cannot download both {target} and {dirs[key]}: "
                "the first would have to be a file and a folder"
            )
    for target in targets:
        rel = target.relative_to(root)
        for suffix in (PARTIAL_SUFFIX, SIDECAR_SUFFIX):
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


def _lookup_datacenter(volume_id: str) -> str:
    from runpod.api.rest import run_rest_request  # pylint: disable=import-outside-toplevel

    from runpod.api.ctl_commands import _path_segment  # pylint: disable=import-outside-toplevel

    data = run_rest_request("GET", f"/v2/network-volumes/{_path_segment(volume_id)}") or {}
    datacenter = data.get("dataCenter") or data.get("dataCenterId")
    if not datacenter:
        raise NetworkVolumeTransferError(
            f"could not find the datacenter of network volume {volume_id}"
        )
    return str(datacenter)


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
            f"the s3 endpoint {host} does not resolve: check --datacenter (only "
            "datacenters with the s3 api are supported) or your network connection"
        ) from exc


def _make_transfer(
    volume_id: str,
    datacenter: Optional[str],
    access_key: Optional[str],
    secret_key: Optional[str],
    endpoint_url: Optional[str],
    concurrency: int,
    progress: Optional[Progress],
    force: bool = False,
) -> _Transfer:
    import boto3  # pylint: disable=import-outside-toplevel
    from botocore.config import Config  # pylint: disable=import-outside-toplevel

    access_key = access_key or os.environ.get(ACCESS_KEY_ENV)
    secret_key = secret_key or os.environ.get(SECRET_KEY_ENV)
    if not access_key or not secret_key:
        raise NetworkVolumeTransferError(
            f"s3 api keys required: set {ACCESS_KEY_ENV} and {SECRET_KEY_ENV} "
            f"(create keys at {S3_KEYS_URL})"
        )
    datacenter = datacenter or _lookup_datacenter(volume_id)
    endpoint = endpoint_url or f"https://s3api-{datacenter.lower()}.runpod.io/"
    _check_endpoint_resolves(endpoint)
    concurrency = max(1, int(concurrency))
    config = Config(
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
    client = boto3.session.Session().client(
        "s3",
        region_name=datacenter,
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=config,
    )
    return _Transfer(client, volume_id, endpoint, concurrency, progress, force=force)


def upload(
    volume_id: str,
    local_path,
    remote_path: Optional[str] = None,
    *,
    datacenter: Optional[str] = None,
    access_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    endpoint_url: Optional[str] = None,
    concurrency: int = 4,
    progress: Optional[Progress] = None,
    force: bool = False,
) -> Dict[str, int]:
    """upload a file or folder to a network volume.

    `remote_path` defaults to the local basename. a folder's contents go
    under `remote_path/`. for a file, a `remote_path` ending in "/" keeps
    the local file name. unchanged files (same size, remote not older, in
    whole seconds) are skipped unless `force`, and an interrupted multipart
    upload resumes on re-run.

    s3 keys come from the arguments or RUNPOD_S3_ACCESS_KEY and
    RUNPOD_S3_SECRET_KEY. without `datacenter`, the volume's datacenter is
    looked up with the runpod api key.

    returns {files_transferred, files_skipped, bytes_transferred}.
    """
    transfer = _make_transfer(
        volume_id, datacenter, access_key, secret_key, endpoint_url, concurrency, progress, force
    )
    return transfer.upload(Path(local_path), remote_path)


def download(
    volume_id: str,
    remote_path: str,
    local_path=None,
    *,
    datacenter: Optional[str] = None,
    access_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    endpoint_url: Optional[str] = None,
    concurrency: int = 4,
    progress: Optional[Progress] = None,
    force: bool = False,
) -> Dict[str, int]:
    """download a file or folder from a network volume.

    a `remote_path` ending in "/" (or one that only exists as a prefix) is
    a folder. `local_path` defaults to the remote basename. local files of
    the same size and not older than the remote copy are skipped (downloads
    stamp the remote time on the file) unless `force`, and partial downloads
    resume on re-run. a `local_path` ending in "/" is a folder to download into.

    returns {files_transferred, files_skipped, bytes_transferred}.
    """
    transfer = _make_transfer(
        volume_id, datacenter, access_key, secret_key, endpoint_url, concurrency, progress, force
    )
    # passed through as given: Path() would drop a trailing "/"
    return transfer.download(remote_path, local_path)


upload_to_network_volume = upload
download_from_network_volume = download
