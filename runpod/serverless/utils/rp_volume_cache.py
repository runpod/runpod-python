"""Bidirectional warm-cache sync between local directories and a network volume."""

import contextlib
import os
import tarfile
import tempfile
import time
import uuid
import threading

from runpod.serverless.modules.rp_logger import RunPodLogger

log = RunPodLogger()

_BASELINE_EPSILON_SECONDS = 2.0  # tolerate coarse (1s) network-filesystem mtime granularity


class VolumeCache:
    """Hydrate configured dirs from a network volume on cold start; sync their
    delta back as a worker-owned tar shard. Best-effort and stdlib-only."""

    _EXCLUDE_SUBSTRINGS = (os.sep + "refs" + os.sep, os.sep + ".no_exist" + os.sep)

    def __init__(
        self,
        dirs,
        *,
        namespace=None,
        volume_path="/runpod-volume",
        max_size_gb=None,
        best_effort=True,
    ):
        self._dirs = [os.path.realpath(os.fspath(d)) for d in dirs]
        self._namespace = namespace or os.environ.get("RUNPOD_ENDPOINT_ID") or ""
        self._volume_path = os.fspath(volume_path)
        self._max_size_gb = max_size_gb
        self._best_effort = best_effort
        self._worker_id = os.environ.get("RUNPOD_POD_ID") or uuid.uuid4().hex[:12]
        self._baseline = time.time() - _BASELINE_EPSILON_SECONDS
        self._lock = threading.Lock()

    @property
    def _shard_dir(self):
        return os.path.join(self._volume_path, ".cache", self._namespace)

    @property
    def available(self):
        return bool(self._namespace) and os.path.isdir(self._volume_path)

    def _list_shards(self):
        d = self._shard_dir
        if not os.path.isdir(d):
            return []
        shards = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".tar")]
        return sorted(shards, key=os.path.getmtime)

    def _iter_delta_files(self):
        for root in self._dirs:
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, files in os.walk(root):
                for name in files:
                    path = os.path.join(dirpath, name)
                    if name.endswith(".lock") or name.startswith(".rp_volume_cache"):
                        continue
                    if any(sub in path for sub in self._EXCLUDE_SUBSTRINGS):
                        continue
                    try:
                        if os.path.getmtime(path) > self._baseline:
                            yield path
                    except OSError:
                        continue

    def _guard(self, fn, default):
        try:
            return fn()
        except Exception as exc:                     # best-effort: never break the worker
            if not self._best_effort:
                raise
            log.warn(f"VolumeCache operation failed: {exc}")
            return default

    def sync(self):
        if not self.available:
            return False
        return self._guard(self._do_sync, False)

    def _do_sync(self):
        files = list(self._iter_delta_files())
        if not files:
            log.debug("VolumeCache: no delta files to sync")
            return False
        os.makedirs(self._shard_dir, exist_ok=True)
        final = os.path.join(self._shard_dir, f"{self._worker_id}-{time.time_ns():020d}.tar")
        tmp = final + ".tmp"
        with tarfile.open(tmp, "w") as tar:
            for path in files:
                tar.add(path, arcname=os.path.relpath(path, "/"))
        os.replace(tmp, final)
        log.info(f"VolumeCache: synced {len(files)} files to {final}")
        self._baseline = time.time() - _BASELINE_EPSILON_SECONDS
        self._enforce_retention()
        return True

    def _enforce_retention(self):
        if not self._max_size_gb:
            return
        cap = self._max_size_gb * (1024 ** 3)
        shards = self._list_shards()                 # oldest first
        total = sum(os.path.getsize(s) for s in shards)
        for shard in shards:
            if total <= cap:
                break
            size = os.path.getsize(shard)
            try:
                os.remove(shard)
                total -= size
                log.info(f"VolumeCache: pruned old shard {shard}")
            except OSError as exc:
                log.warn(f"VolumeCache: failed to prune {shard}: {exc}")

    @property
    def _marker_path(self):
        base = os.path.join(tempfile.gettempdir(), "rp_volume_cache")
        return os.path.join(base, f"{self._namespace}.hydrated")

    def _newest_shard_mtime(self):
        shards = self._list_shards()
        return os.path.getmtime(shards[-1]) if shards else 0.0

    def _clear_marker_for_test(self):
        if os.path.exists(self._marker_path):
            os.remove(self._marker_path)

    def hydrate(self):
        if not self.available:
            return False
        return self._guard(self._do_hydrate, False)

    def _do_hydrate(self):
        shards = self._list_shards()
        if not shards:
            return False
        newest = self._newest_shard_mtime()
        if os.path.exists(self._marker_path) and os.path.getmtime(self._marker_path) >= newest:
            log.debug("VolumeCache: cache already hydrated, skipping")
            return False
        extracted = False
        # Use the tar data filter for defense-in-depth where the runtime provides
        # it (Python 3.12+, and 3.10.12+/3.11.4+ backports) without requiring it --
        # the >=3.10 floor may predate the API. _is_safe_member is the primary guard.
        extract_kwargs = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        for shard in shards:                       # oldest -> newest (last wins)
            with tarfile.open(shard) as tar:
                safe = [m for m in tar.getmembers() if self._is_safe_member(m)]
                tar.extractall(path="/", members=safe, **extract_kwargs)
                extracted = extracted or bool(safe)
        os.makedirs(os.path.dirname(self._marker_path), exist_ok=True)
        with open(self._marker_path, "w") as fh:
            fh.write(str(newest))
        os.utime(self._marker_path, (newest, newest))
        self._baseline = time.time() - _BASELINE_EPSILON_SECONDS
        if extracted:
            log.info(f"VolumeCache: hydrated from {len(shards)} shard(s)")
        return extracted

    def _is_safe_member(self, member):
        if not (member.isfile() or member.isdir()):
            return False                              # reject symlink/hardlink/device/fifo
        target = os.path.realpath(os.path.join("/", member.name))
        return any(
            target == d or target.startswith(d + os.sep)
            for d in self._dirs
        )

    @contextlib.contextmanager
    def warm(self):
        self.hydrate()
        try:
            yield self
        finally:
            self.sync()
