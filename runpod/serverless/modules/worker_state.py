"""
Handles getting stuff from environment variables and updating the global state like job id.
"""

import os
import time
import uuid
import pickle
import tempfile
from typing import Any, Dict, Optional, Set

from filelock import FileLock

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
class JobsProgress(Set[Job]):
    """Track the state of current jobs in progress with persistent state."""

    _instance = None
    _STATE_DIR = os.getcwd()
    _STATE_FILE = os.path.join(_STATE_DIR, ".runpod_jobs.pkl")

    def __new__(cls):
        if JobsProgress._instance is None:
            os.makedirs(cls._STATE_DIR, exist_ok=True)
            JobsProgress._instance = set.__new__(cls)
            # Initialize as empty set before loading state
            set.__init__(JobsProgress._instance)
            JobsProgress._instance._load_state()
        return JobsProgress._instance

    def __init__(self):
        # This should never clear data in a singleton
        # Don't call parent __init__ as it would clear the set
        pass
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>: {self.get_job_list()}"

    def _load_state(self):
        """Load jobs state from pickle file with file locking."""
        try:
            if (
                os.path.exists(self._STATE_FILE)
                and os.path.getsize(self._STATE_FILE) > 0
            ):
                with FileLock(self._STATE_FILE + '.lock'):
                    with open(self._STATE_FILE, "rb") as f:
                        try:
                            loaded_jobs = pickle.load(f)
                            # Clear current state and add loaded jobs
                            super().clear()
                            for job in loaded_jobs:
                                set.add(
                                    self, job
                                )  # Use set.add to avoid triggering _save_state

                        except (EOFError, pickle.UnpicklingError):
                            # Handle empty or corrupted file
                            log.debug(
                                "JobsProgress: Failed to load state file, starting with empty state"
                            )
                            pass

        except FileNotFoundError:
            log.debug("JobsProgress: No state file found, starting with empty state")
            pass

    def _save_state(self):
        """Save jobs state to pickle file with atomic write and file locking."""
        try:
            # Use temporary file for atomic write
            with FileLock(self._STATE_FILE + '.lock'):
                with tempfile.NamedTemporaryFile(
                    dir=self._STATE_DIR, delete=False, mode="wb"
                ) as temp_f:
                    pickle.dump(set(self), temp_f)
                
                # Atomically replace the state file
                os.replace(temp_f.name, self._STATE_FILE)
        except Exception as e:
            log.error(f"Failed to save job state: {e}")

    def clear(self) -> None:
        super().clear()
        self._save_state()

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

        result = super().add(element)
        self._save_state()
        return result

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

        result = super().discard(element)
        self._save_state()
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
        Returns the list of job IDs as comma-separated string.
        """
        self._load_state()

        if not len(self):
            return None

        return ",".join(str(job) for job in self)

    def get_job_count(self) -> int:
        """
        Returns the number of jobs.
        """
        return len(self)
