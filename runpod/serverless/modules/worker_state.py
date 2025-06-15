import os
import time
import uuid
import threading
from typing import Any, Dict, Optional, Set

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



class JobsProgress:
    """
    OPTIMIZED: Track jobs in progress with O(1) operations using threading.Lock
    instead of multiprocessing.Manager for better performance.
    """
    
    _instance: Optional['JobsProgress'] = None
    _jobs: Dict[str, Dict[str, Any]]
    _lock: threading.Lock
    _count: int

    def __new__(cls):
        if cls._instance is None:
            instance = object.__new__(cls)
            # Initialize with threading.Lock (much faster than multiprocessing)
            instance._jobs = {}
            instance._lock = threading.Lock()
            instance._count = 0
            cls._instance = instance
        return cls._instance

    def __init__(self):
        # Everything is already initialized in __new__
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>: {self.get_job_list()}"

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()
            self._count = 0

    def add(self, element: Any):
        """
        OPTIMIZED: O(1) addition of jobs using dict
        """
        if isinstance(element, str):
            job_id = element
            job_dict = {'id': element}
        elif isinstance(element, dict):
            job_id = element.get('id')
            job_dict = element
        elif hasattr(element, 'id'):
            job_id = element.id
            job_dict = {'id': element.id}
        else:
            raise TypeError("Only Job objects can be added to JobsProgress.")

        with self._lock:
            if job_id not in self._jobs:
                self._jobs[job_id] = job_dict
                self._count += 1
                log.debug(f"JobsProgress | Added job: {job_id}")

    def get(self, element: Any) -> Optional[Job]:
        """
        retrieval using dict lookup
        """
        if isinstance(element, str):
            search_id = element
        elif isinstance(element, Job):
            search_id = element.id
        else:
            raise TypeError("Only Job objects can be retrieved from JobsProgress.")

        with self._lock:
            job_dict = self._jobs.get(search_id)
            if job_dict:
                log.debug(f"JobsProgress | Retrieved job: {search_id}")
                return Job(**job_dict)
        
        return None

    def remove(self, element: Any):
        """
        removal using dict
        """
        if isinstance(element, str):
            job_id = element
        elif isinstance(element, dict):
            job_id = element.get('id')
        elif hasattr(element, 'id'):
            job_id = element.id
        else:
            raise TypeError("Only Job objects can be removed from JobsProgress.")

        with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                self._count -= 1
                log.debug(f"JobsProgress | Removed job: {job_id}")

    def get_job_list(self) -> Optional[str]:
        """
        Returns the list of job IDs as comma-separated string.
        """
        with self._lock:
            if not self._jobs:
                return None
            
            job_ids = list(self._jobs.keys())
        
        log.debug(f"JobsProgress | Jobs in progress: {job_ids}")
        return ",".join(job_ids)

    def get_job_count(self) -> int:
        """
        count operation
        """
        # No lock needed for reading an int (atomic operation)
        return self._count

    def __iter__(self):
        """Make the class iterable - returns Job objects"""
        with self._lock:
            # Create a snapshot to avoid holding lock during iteration
            job_dicts = list(self._jobs.values())
        
        # Return an iterator of Job objects
        return iter(Job(**job_dict) for job_dict in job_dicts)

    def __len__(self):
        """Support len() operation"""
        return self._count

    def __contains__(self, element: Any) -> bool:
        """
        membership test using dict
        """
        if isinstance(element, str):
            search_id = element
        elif isinstance(element, Job):
            search_id = element.id
        elif isinstance(element, dict):
            search_id = element.get('id')
        else:
            return False

        with self._lock:
            return search_id in self._jobs


