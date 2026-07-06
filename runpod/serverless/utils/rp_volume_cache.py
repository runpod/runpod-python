"""Bidirectional warm-cache sync between local directories and a network volume."""

import os
import time
import uuid
import threading

from runpod.serverless.modules.rp_logger import RunPodLogger

log = RunPodLogger()


class VolumeCache:
    """Hydrate configured dirs from a network volume on cold start; sync their
    delta back as a worker-owned tar shard. Best-effort and stdlib-only."""

    def __init__(
        self,
        dirs,
        *,
        namespace=None,
        volume_path="/runpod-volume",
        max_size_gb=None,
        best_effort=True,
    ):
        self._dirs = [os.path.abspath(os.fspath(d)) for d in dirs]
        self._namespace = namespace or os.environ.get("RUNPOD_ENDPOINT_ID") or ""
        self._volume_path = os.fspath(volume_path)
        self._max_size_gb = max_size_gb
        self._best_effort = best_effort
        self._worker_id = os.environ.get("RUNPOD_POD_ID") or uuid.uuid4().hex[:12]
        self._baseline = time.time()
        self._lock = threading.Lock()

    @property
    def _shard_dir(self):
        return os.path.join(self._volume_path, ".cache", self._namespace)

    @property
    def available(self):
        return bool(self._namespace) and os.path.isdir(self._volume_path)
