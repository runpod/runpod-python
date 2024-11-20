"""
Handles getting stuff from environment variables and updating the global state like job id.
"""

import os
import time
import uuid
from typing import Any, Dict, Optional
from asyncio import Queue, Lock

from .rp_logger import RunPodLogger


log = RunPodLogger()

REF_COUNT_ZERO = time.perf_counter()  # Used for benchmarking with the debugger.

WORKER_ID = os.environ.get("RUNPOD_POD_ID", str(uuid.uuid4()))


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
#                                    Tracker                                   #
# ---------------------------------------------------------------------------- #
class JobsProgress(set):
    """Track the state of current jobs in progress."""

    _instance = None

    def __new__(cls):
        if JobsProgress._instance is None:
            JobsProgress._instance = set.__new__(cls)
        return JobsProgress._instance

    def __init__(self):
        if not hasattr(self, "_lock"):
            # Initialize the lock once
            self._lock = Lock()

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>: {self.get_job_list()}"

    async def clear(self) -> None:
        async with self._lock:
            return super().clear()

    async def add(self, element: Any):
        """
        Adds a Job object to the set.

        If the added element is a string, then `Job(id=element)` is added
        
        If the added element is a dict, that `Job(**element)` is added
        """
        if isinstance(element, str):
            element = Job(id=element)

        if isinstance(element, dict):
            element = Job(**element)

        if not isinstance(element, Job):
            raise TypeError("Only Job objects can be added to JobsProgress.")

        async with self._lock:
            log.debug(f"JobsProgress.add", element.id)
            super().add(element)

    async def remove(self, element: Any):
        """
        Removes a Job object from the set.

        If the element is a string, then `Job(id=element)` is removed
        
        If the element is a dict, then `Job(**element)` is removed
        """
        if isinstance(element, str):
            element = Job(id=element)

        if isinstance(element, dict):
            element = Job(**element)

        if not isinstance(element, Job):
            raise TypeError("Only Job objects can be removed from JobsProgress.")

        async with self._lock:
            log.debug(f"JobsProgress.remove", element.id)
            return super().discard(element)

    async def get(self, element: Any) -> Job:
        if isinstance(element, str):
            element = Job(id=element)

        if not isinstance(element, Job):
            raise TypeError("Only Job objects can be retrieved from JobsProgress.")

        async with self._lock:
            for job in self:
                if job == element:
                    return job

    def get_job_list(self) -> str:
        """
        Returns the list of job IDs as comma-separated string.
        """
        if not len(self):
            return None

        return ",".join(str(job) for job in self)

    async def get_job_count(self) -> int:
        """
        Returns the number of jobs in a thread-safe manner.
        """
        async with self._lock:
            return len(self)


class JobsQueue(Queue):
    """Central Jobs Queue for job take and job processing"""

    _instance = None

    def __new__(cls):
        if JobsQueue._instance is None:
            JobsQueue._instance = object.__new__(cls)
        return JobsQueue._instance

    def __iter__(self):
        return iter(list(self._queue))

    async def add_job(self, job: dict):
        """
        Adds a job to the queue.

        If the queue is full, wait until a free
        slot is available before adding item.
        """
        log.debug(f"JobsQueue.add_job", job["id"])
        return await self.put(job)

    async def get_job(self) -> dict:
        """
        Remove and return the next job from the queue.

        If queue is empty, wait until a job is available.

        Note: make sure to call `.task_done()` when processing the job is finished.
        """
        return await self.get()

    def get_job_list(self) -> Optional[str]:
        """
        Returns the comma-separated list of jobs as a string. (read-only)
        """
        if self.empty():
            return None

        return ",".join(job.get("id") for job in self)

    def get_job_count(self) -> int:
        """
        Returns the number of jobs.
        """
        return self.qsize()

    async def clear(self):
        """
        Empties the Queue by getting each item.
        """
        while not self.empty():
            await self.get()
            self.task_done()
