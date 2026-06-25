"""
Handles getting stuff from environment variables and updating the global state like job id.
"""

import multiprocessing
import os
import time
import uuid
from typing import Any, Dict, Optional, Set

from .rp_logger import RunPodLogger


log = RunPodLogger()

REF_COUNT_ZERO = time.perf_counter()  # Used for benchmarking with the debugger.

WORKER_ID = os.environ.get("RUNPOD_POD_ID", str(uuid.uuid4()))

PING_MIRROR_CAPACITY = 65536  # bytes; ample headroom for a job-id snapshot


# ----------------------------------- Flags ---------------------------------- #
IS_LOCAL_TEST = os.environ.get("RUNPOD_WEBHOOK_GET_JOB", None) is None


# ------------------------------- Job Tracking ------------------------------- #
class Job:
    """
    Represents a job object.

    Args:
        job_id: The id of the job, a unique string.
        job_input: The input to the job.
        webhook: The webhook to send the job output to.
    """

    def __init__(
        self,
        id: str,
        input: Optional[Dict[str, Any]] = None,
        webhook: Optional[str] = None,
        **kwargs
    ) -> None:
        self.id = id
        self.input = input
        self.webhook = webhook

        for key, value in kwargs.items():
            setattr(self, key, value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Job):
            return self.id == other.id
        return False

    def __hash__(self) -> int:
        return hash(self.id)

    def __str__(self) -> str:
        return self.id


# ---------------------------------------------------------------------------- #
#                                    Tracker                                    #
# ---------------------------------------------------------------------------- #
class JobsProgress(Set[Job]):
    """Track the state of current jobs in progress (in-memory, per process)."""

    _instance = None

    def __new__(cls):
        if JobsProgress._instance is None:
            JobsProgress._instance = set.__new__(cls)
            set.__init__(JobsProgress._instance)
            # One-way snapshot to the ping process; attached in the main
            # process via set_mirror(). Stays None off-Runpod and in tests.
            JobsProgress._instance._mirror = None
        return JobsProgress._instance

    def __init__(self):
        # Singleton: never re-initialize, it would clear the set.
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>: {self.get_job_list()}"

    def set_mirror(self, mirror) -> None:
        """Attach a PingJobMirror that mirrors the in-progress job ids to the
        ping process. Every add/remove/clear then pushes the snapshot to it."""
        self._mirror = mirror
        self._notify_mirror()

    def _notify_mirror(self) -> None:
        """Push the current job-id snapshot to the attached mirror, if any."""
        if self._mirror is not None:
            self._mirror.set(self.get_job_list())

    def clear(self) -> None:
        super().clear()
        self._notify_mirror()

    def add(self, element: Any):
        """
        Adds a Job object to the set.

        If the added element is a string, then `Job(id=element)` is added.
        If the added element is a dict, then `Job(**element)` is added.
        """
        if isinstance(element, str):
            element = Job(id=element)

        if isinstance(element, dict):
            element = Job(**element)

        if not isinstance(element, Job):
            raise TypeError("Only Job objects can be added to JobsProgress.")

        result = super().add(element)
        self._notify_mirror()
        return result

    def remove(self, element: Any):
        """
        Removes a Job object from the set.

        If the element is a string, then `Job(id=element)` is removed.
        If the element is a dict, then `Job(**element)` is removed.
        """
        if isinstance(element, str):
            element = Job(id=element)

        if isinstance(element, dict):
            element = Job(**element)

        if not isinstance(element, Job):
            raise TypeError("Only Job objects can be removed from JobsProgress.")

        result = super().discard(element)
        self._notify_mirror()
        return result

    def get(self, element: Any) -> Optional[Job]:
        if isinstance(element, str):
            element = Job(id=element)

        if not isinstance(element, Job):
            raise TypeError("Only Job objects can be retrieved from JobsProgress.")

        for job in self:
            if job == element:
                return job
        return None

    def get_job_list(self) -> Optional[str]:
        """
        Returns the list of job IDs as a comma-separated string, or None if empty.
        """
        if not len(self):
            return None

        return ",".join(str(job) for job in self)

    def get_job_count(self) -> int:
        """
        Returns the number of jobs.
        """
        return len(self)


# ---------------------------------------------------------------------------- #
#                              Ping Job Mirror                                  #
# ---------------------------------------------------------------------------- #
class PingJobMirror:
    """
    One-way snapshot of in-progress job ids from the worker (main) process to
    the separate ping process.

    Backed by a fixed-size shared-memory buffer created in the main process and
    passed to the ping process via ``Process(args=...)``. It lives only in this
    worker's own process tree, so it cannot be shared across workers and never
    touches the filesystem. All operations are best-effort and never raise into
    the caller (a failure here must not break job processing or kill the ping).
    """

    def __init__(self, capacity: int = PING_MIRROR_CAPACITY, ctx=None):
        ctx = ctx or multiprocessing
        self._capacity = capacity
        self._buffer = ctx.Array("c", capacity)  # SynchronizedString with .get_lock()

    def set(self, job_ids: Optional[str]) -> None:
        """Write the current job-id snapshot. Best-effort; never raises."""
        try:
            data = (job_ids or "").encode("utf-8")
            limit = self._capacity - 1  # reserve a byte for the NUL terminator
            if len(data) > limit:
                data = data[:limit]
                cut = data.rfind(b",")
                if cut != -1:
                    data = data[:cut]
                log.warn(
                    f"PingJobMirror: job-id snapshot exceeded {limit} bytes; truncated"
                )
            with self._buffer.get_lock():
                self._buffer.value = data
        except Exception as err:  # never break job processing
            log.error(f"PingJobMirror.set failed: {err}")

    def get(self) -> Optional[str]:
        """Read the current job-id snapshot. Best-effort; never raises."""
        try:
            with self._buffer.get_lock():
                data = self._buffer.value
            text = data.decode("utf-8")
            return text or None
        except Exception as err:  # never kill the ping loop
            log.debug(f"PingJobMirror.get failed: {err}")
            return None
