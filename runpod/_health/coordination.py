"""Linux container-start-scoped coordination for shared health checks."""

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path


class CoordinationUnavailable(Exception):
    """Shared state cannot be used; worker-start checks remain available."""


class CoordinationBusy(Exception):
    """Another process did not finish within the bounded wait."""


def container_start_id() -> str:
    """PID namespace + init start ticks + host boot distinguish container restarts.

    Use fixed /tmp rather than TMPDIR (which can differ between processes).
    No pod-id-only marker: container files can survive a restart.
    """
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    stat = Path("/proc/1/stat").read_text()
    start_ticks = stat.rsplit(")", 1)[1].split()[19]
    namespace = os.readlink("/proc/1/ns/pid")
    return hashlib.sha256(f"{boot}:{namespace}:{start_ticks}".encode()).hexdigest()


class ContainerChecks:
    """Hold one flock while reading, executing, and recording early checks."""

    def __init__(self, timeout: float = 35):
        self.timeout = timeout
        self.fd = None
        self.state = {"passed": [], "failure": None}

    async def __aenter__(self):
        try:
            import fcntl

            identity = container_start_id()
            path = f"/tmp/runpod-fitness-{identity}.json"
            self.fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.set_inheritable(self.fd, False)
        except (OSError, ValueError, IndexError, ImportError) as exc:
            self.close()
            raise CoordinationUnavailable(str(exc)) from exc
        try:
            await self._acquire_lock(fcntl)
            self._load_state()
            return self
        except (OSError, ValueError) as exc:
            self.close()
            raise CoordinationUnavailable(str(exc)) from exc
        except BaseException:
            self.close()
            raise

    async def _acquire_lock(self, fcntl) -> None:
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CoordinationBusy("Timed out waiting for early health checks")
                await asyncio.sleep(0.05)

    def _load_state(self) -> None:
        raw = os.read(self.fd, 65536)
        if not raw:
            return
        state = json.loads(raw)
        if not isinstance(state, dict):
            raise ValueError("Health-check state must be an object")
        passed = state.get("passed")
        if not isinstance(passed, list) or not all(
            isinstance(key, str) for key in passed
        ):
            raise ValueError("Passed health checks must be a list of cache keys")
        if not isinstance(state.get("failure"), (str, type(None))):
            raise ValueError("Health-check failure must be a string or null")
        self.state = state

    def save(self) -> None:
        """Persist before releasing the lock or terminating on failure."""
        data = json.dumps(self.state).encode()
        os.lseek(self.fd, 0, os.SEEK_SET)
        remaining = memoryview(data)
        while remaining:
            written = os.write(self.fd, remaining)
            if written <= 0:
                raise OSError("Unable to persist health-check state")
            remaining = remaining[written:]
        os.ftruncate(self.fd, len(data))
        os.fsync(self.fd)

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    async def __aexit__(self, *args):
        self.close()
