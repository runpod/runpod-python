"""Filesystem and checksum helpers for resumable transfers.

This private module keeps disk state and path safety details out of the S3
request orchestration. network_volume_transfer re-exports the error type.
"""

import hashlib
import json
import os
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

MIB = 1024 * 1024
PARTIAL_SUFFIX = ".rpdownload"
SIDECAR_SUFFIX = ".rpdownload.json"
# every file a download writes next to its target
TRANSFER_SUFFIXES = (PARTIAL_SUFFIX, SIDECAR_SUFFIX, SIDECAR_SUFFIX + ".tmp")


class NetworkVolumeTransferError(RuntimeError):
    """a transfer failed. `resumable` says whether re-running can help."""

    def __init__(self, message: str, resumable: bool = False):
        super().__init__(message)
        self.resumable = resumable


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
        # some filesystems refuse fsync on a directory; the rename stands
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
