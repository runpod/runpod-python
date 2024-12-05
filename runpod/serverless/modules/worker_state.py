"""
Handles getting stuff from environment variables and updating the global state like job id.
"""

import os
import time
import uuid
from typing import Any, Dict, Optional

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

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>: {self.get_job_list()}"

    def clear(self) -> None:
        return super().clear()

    def add(self, element: Any):
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

        return super().add(element)

    def remove(self, element: Any):
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

        return super().discard(element)

    def get(self, element: Any) -> Job:
        if isinstance(element, str):
            element = Job(id=element)

        if not isinstance(element, Job):
            raise TypeError("Only Job objects can be retrieved from JobsProgress.")

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

    def get_job_count(self) -> int:
        """
        Returns the number of jobs.
        """
        return len(self)
