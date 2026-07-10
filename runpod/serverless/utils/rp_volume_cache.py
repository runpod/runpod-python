"""Directory-mirror warm cache between local directories and a network volume.

``VolumeCache`` keeps a browsable mirror of one or more local directories on a
mounted network volume, and reconciles the two directions on demand:

- ``hydrate()``: copy files that are missing or newer on the volume mirror
  into the container (used on cold start, before the cache is populated).
- ``sync()``: copy files that are missing or newer in the container onto the
  volume mirror (used after the cache has been populated/updated).

Both directions are per-file, atomic (write-to-temp + ``os.replace``), and
idempotent: unchanged files are skipped on repeat calls because ``shutil.copy2``
preserves mtimes. Stdlib-only and best-effort: any failure degrades to a cold
worker and never raises into the caller (unless ``best_effort=False``).
"""

import atexit
import json
import os
import shutil
import threading

from runpod.serverless.modules.rp_logger import RunPodLogger

log = RunPodLogger()

_MTIME_TOLERANCE = 2.0  # seconds; tolerate coarse (NFS) mtime granularity
_JOIN_TIMEOUT_SECONDS = 30.0  # bound the atexit wait so stalled volume I/O can't hang shutdown
_SMALL_FILE_THRESHOLD = 256 * 1024  # bytes; <-threshold packs into one tar (metadata collapse)
_FORMAT_VERSION = 1
_MANIFEST_NAME = "manifest.json"
_SMALL_ARCHIVE_NAME = "small.tar"
_BIG_SUBDIR = "big"


class VolumeCache:
    """Warm-cache local directories across serverless workers via a network volume.

    Maintains a mirror of ``dirs`` at ``{volume_path}/.cache/{namespace}``.
    ``hydrate()`` reconciles volume -> container; ``sync()`` reconciles
    container -> volume. Used as a context manager, the object is itself the
    "warm cache" closure: hydrate on enter, sync (in the background by
    default) on exit.

    Requires a network volume mounted at ``volume_path`` (default
    ``/runpod-volume``) and a non-empty ``namespace`` (default
    ``RUNPOD_ENDPOINT_ID``); otherwise ``available`` is False and all
    operations are no-ops.

    Args:
        dirs: Local directories to cache (e.g. a model cache like ``HF_HOME``).
        namespace: Isolation key for the on-volume mirror. Must be a single
            safe path component. Defaults to ``RUNPOD_ENDPOINT_ID``.
        volume_path: Network-volume mount point. Defaults to ``/runpod-volume``.
        best_effort: When True (default), swallow and log errors instead of
            raising.

    Example:
        >>> with VolumeCache(dirs=["/root/.cache/huggingface"]):
        ...     model = load_model()  # downloads land in the cached dir
    """

    _EXCLUDE_SUBSTRINGS = (os.sep + "refs" + os.sep, os.sep + ".no_exist" + os.sep)

    def __init__(
        self, dirs, *, namespace=None, volume_path="/runpod-volume", best_effort=True, max_workers=None
    ):
        self._dirs = [os.path.realpath(os.fspath(d)) for d in dirs]
        self._namespace = namespace or os.environ.get("RUNPOD_ENDPOINT_ID") or ""
        if self._namespace and (
            os.path.isabs(self._namespace)
            or os.sep in self._namespace
            or "/" in self._namespace
            or "\\" in self._namespace
            or self._namespace in (".", "..")
        ):
            raise ValueError(
                f"namespace must be a single safe path component, got {self._namespace!r}"
            )
        self._volume_path = os.fspath(volume_path)
        self._best_effort = best_effort
        self._max_workers = max_workers or min(32, (os.cpu_count() or 4) * 4)

    @property
    def _mirror_root(self):
        return os.path.join(self._volume_path, ".cache", self._namespace)

    @property
    def _manifest_path(self):
        return os.path.join(self._mirror_root, _MANIFEST_NAME)

    @property
    def _small_archive_path(self):
        return os.path.join(self._mirror_root, _SMALL_ARCHIVE_NAME)

    @property
    def _big_root(self):
        return os.path.join(self._mirror_root, _BIG_SUBDIR)

    @property
    def available(self):
        """True iff volume_path is a mounted dir AND namespace is non-empty."""
        return bool(self._namespace) and os.path.isdir(self._volume_path)

    # ----------------------------------------------------------------- #
    # reconcile helpers
    # ----------------------------------------------------------------- #

    def _iter_files(self, root):
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(dirpath, name)
                if name.endswith(".lock") or name.endswith(".rpvc.tmp"):
                    continue
                if any(sub in path for sub in self._EXCLUDE_SUBSTRINGS):
                    continue
                try:
                    if os.path.islink(path):
                        continue
                except OSError:
                    continue
                yield path

    def _partition(self, paths):
        small, big = [], []
        for p in paths:
            try:
                size = os.stat(p).st_size
            except OSError:
                continue
            (small if size < _SMALL_FILE_THRESHOLD else big).append(p)
        return small, big

    def _file_meta(self, path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        return {"path": path, "size": st.st_size, "mtime": st.st_mtime}

    def _write_manifest(self, small_meta, big_meta):
        os.makedirs(self._mirror_root, exist_ok=True)
        payload = {
            "version": _FORMAT_VERSION,
            "threshold": _SMALL_FILE_THRESHOLD,
            "small": small_meta,
            "big": big_meta,
        }
        tmp = f"{self._manifest_path}.{os.getpid()}.{threading.get_ident()}.rpvc.tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, self._manifest_path)

    def _read_manifest(self):
        try:
            with open(self._manifest_path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _meta_satisfied_by_local(self, meta):
        try:
            st = os.stat(meta["path"])
        except OSError:
            return False
        return st.st_size == meta["size"] and st.st_mtime >= meta["mtime"] - _MTIME_TOLERANCE

    def _changed_vs_manifest(self, metas, manifest, key):
        prior = {e["path"]: e for e in (manifest.get(key, []) if manifest else [])}
        changed = []
        for m in metas:
            p = prior.get(m["path"])
            if p is None or p["size"] != m["size"] or m["mtime"] > p["mtime"] + _MTIME_TOLERANCE:
                changed.append(m)
        return changed

    @staticmethod
    def _needs_copy(src_path, dst_path):
        try:
            s = os.stat(src_path)
        except OSError:
            return False
        try:
            d = os.stat(dst_path)
        except OSError:
            return True
        return s.st_size != d.st_size or s.st_mtime > d.st_mtime + _MTIME_TOLERANCE

    def _is_safe_dest(self, dst_abs):
        target = os.path.realpath(dst_abs)
        return any(target == d or target.startswith(d + os.sep) for d in self._dirs)

    def _copy_file(self, src, dst):
        tmp = f"{dst}.{os.getpid()}.{threading.get_ident()}.rpvc.tmp"
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, tmp)  # preserves mtime so future diffs converge
            os.replace(tmp, dst)  # atomic; last-writer-wins under concurrency
            return True
        except OSError as exc:
            log.debug(f"VolumeCache: skip {src} -> {dst}: {exc}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                # best-effort cleanup; a leftover temp file is overwritten next sync
                pass
            return False

    def _guard(self, fn, default):
        try:
            return fn()
        except Exception as exc:  # best-effort: never break the worker
            if not self._best_effort:
                raise
            log.warn(f"VolumeCache operation failed: {exc}")
            return default

    # ----------------------------------------------------------------- #
    # hydrate / sync
    # ----------------------------------------------------------------- #

    def hydrate(self):
        """Reconcile volume mirror -> container.

        Copy files missing or newer in the container. Returns the number of
        files copied. No-op (0) if unavailable.
        """
        if not self.available:
            return 0
        return self._guard(self._do_hydrate, 0)

    def _do_hydrate(self):
        root = self._mirror_root
        if not os.path.isdir(root):
            return 0
        copied = 0
        for m in self._iter_files(root):
            dst = os.path.join("/", os.path.relpath(m, root))
            if not self._is_safe_dest(dst):
                continue  # skip anything that would escape configured dirs
            if self._needs_copy(m, dst) and self._copy_file(m, dst):
                copied += 1
        if copied:
            log.info(f"VolumeCache: hydrated {copied} file(s) from {root}")
        return copied

    def sync(self, *, background=True):
        """Reconcile container -> volume mirror.

        Copy files missing or newer on the volume. When ``background=True``
        (default), run on a daemon thread and return immediately; a
        process-exit hook joins outstanding syncs so short-lived processes
        still complete. When False, run inline and return when done.
        """
        if not self.available:
            return
        if background:
            t = threading.Thread(target=lambda: self._guard(self._do_sync, 0), daemon=True)
            _register_pending(t)
            t.start()
        else:
            self._guard(self._do_sync, 0)

    def _do_sync(self):
        os.makedirs(self._mirror_root, exist_ok=True)
        copied = 0
        for root in self._dirs:
            if not os.path.isdir(root):
                continue
            for f in self._iter_files(root):
                dst = os.path.join(self._mirror_root, os.path.relpath(f, "/"))
                if self._needs_copy(f, dst) and self._copy_file(f, dst):
                    copied += 1
        if copied:
            log.info(f"VolumeCache: synced {copied} file(s) to {self._mirror_root}")
        return copied

    # ----------------------------------------------------------------- #
    # context manager
    # ----------------------------------------------------------------- #

    def __enter__(self):
        self.hydrate()
        return self

    def __exit__(self, *exc):
        self.sync(background=True)
        return None


# ----------------------------------------------------------------------- #
# background sync completion
# ----------------------------------------------------------------------- #

_pending_syncs = []


def _register_pending(thread):
    _pending_syncs[:] = [t for t in _pending_syncs if t.is_alive()]
    _pending_syncs.append(thread)


def _join_pending_syncs():
    for t in list(_pending_syncs):
        try:
            t.join(timeout=_JOIN_TIMEOUT_SECONDS)
            if t.is_alive():
                log.warn(
                    "VolumeCache: background sync did not finish within "
                    f"{_JOIN_TIMEOUT_SECONDS:.0f}s at exit; cache may be incomplete"
                )
        except Exception:
            # best-effort shutdown: a join failure must not block interpreter exit
            pass
    _pending_syncs.clear()


def _reset_pending_for_test():
    _pending_syncs.clear()


atexit.register(_join_pending_syncs)
