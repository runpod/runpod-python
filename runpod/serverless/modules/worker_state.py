"""
Handles getting stuff from environment variables and updating the global state like job id.
"""

import os
import time
import uuid
import threading
from multiprocessing import Manager
from multiprocessing.managers import SyncManager
from typing import Any, Dict, Optional

from .rp_logger import RunPodLogger


log = RunPodLogger()


# ----------------------------- Lazy Loading Utilities -------------------------- #
_jobs_progress_instance = None


def get_jobs_progress():
    """Get the global JobsProgress instance with lazy initialization."""
    global _jobs_progress_instance
    if _jobs_progress_instance is None:
        _jobs_progress_instance = JobsProgress()
    return _jobs_progress_instance


def reset_jobs_progress():
    """Reset the lazy-loaded JobsProgress instance (useful for testing)."""
    global _jobs_progress_instance
    _jobs_progress_instance = None

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
class JobsProgress:
    """Track the state of current jobs in progress using shared memory or thread-safe fallback."""
    
    _instance: Optional['JobsProgress'] = None
    _manager: Optional[SyncManager] = None
    _shared_data: Optional[Any] = None
    _lock: Optional[Any] = None
    _use_multiprocessing: bool = True
    _fallback_jobs: list = []
    _fallback_lock: Optional[threading.Lock] = None

    def __new__(cls):
        if cls._instance is None:
            instance = object.__new__(cls)
            # Initialize multiprocessing objects directly like the original
            try:
                instance._manager = Manager()
                instance._shared_data = instance._manager.dict()
                instance._shared_data['jobs'] = instance._manager.list()
                instance._lock = instance._manager.Lock()
                instance._use_multiprocessing = True
                log.debug("JobsProgress | Using multiprocessing for GIL-free operation")
            except Exception as e:
                log.warn(f"JobsProgress | Multiprocessing failed ({e}), falling back to thread-safe mode")
                instance._fallback_jobs = []
                instance._fallback_lock = threading.Lock()
                instance._use_multiprocessing = False
            cls._instance = instance
        return cls._instance

    def __init__(self):
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>: {self.get_job_list()}"

    def clear(self) -> None:
        if self._use_multiprocessing and self._lock is not None and self._shared_data is not None:
            with self._lock:
                self._shared_data['jobs'][:] = []
        elif not self._use_multiprocessing and self._fallback_lock is not None:
            with self._fallback_lock:
                self._fallback_jobs.clear()

    def add(self, element: Any):
        """
        Adds a Job object to the set.
        """
        if isinstance(element, str):
            job_dict = {'id': element}
        elif isinstance(element, dict):
            job_dict = element
        elif hasattr(element, 'id'):
            job_dict = {'id': element.id}
        else:
            raise TypeError("Only Job objects can be added to JobsProgress.")

        if self._use_multiprocessing and self._lock is not None and self._shared_data is not None:
            with self._lock:
                job_list = self._shared_data['jobs']
                if not any(job['id'] == job_dict['id'] for job in job_list):
                    job_list.append(job_dict)
        elif not self._use_multiprocessing and self._fallback_lock is not None:
            with self._fallback_lock:
                if not any(job['id'] == job_dict['id'] for job in self._fallback_jobs):
                    self._fallback_jobs.append(job_dict)
        
        log.debug(f"JobsProgress | Added job: {job_dict['id']}")

    def get(self, element: Any) -> Optional[Job]:
        """
        Retrieves a Job object from the set.
        
        If the element is a string, searches for Job with that id.
        """
        if isinstance(element, str):
            search_id = element
        elif isinstance(element, Job):
            search_id = element.id
        else:
            raise TypeError("Only Job objects can be retrieved from JobsProgress.")

        if self._use_multiprocessing and self._lock is not None and self._shared_data is not None:
            with self._lock:
                for job_dict in self._shared_data['jobs']:
                    if job_dict['id'] == search_id:
                        log.debug(f"JobsProgress | Retrieved job: {job_dict['id']}")
                        return Job(**job_dict)
        elif not self._use_multiprocessing and self._fallback_lock is not None:
            with self._fallback_lock:
                for job_dict in self._fallback_jobs:
                    if job_dict['id'] == search_id:
                        log.debug(f"JobsProgress | Retrieved job: {job_dict['id']}")
                        return Job(**job_dict)
        return None

    def remove(self, element: Any):
        """
        Removes a Job object from the set.
        """
        if isinstance(element, str):
            job_id = element
        elif isinstance(element, dict):
            job_id = element.get('id')
        elif hasattr(element, 'id'):
            job_id = element.id
        else:
            raise TypeError("Only Job objects can be removed from JobsProgress.")

        if self._use_multiprocessing and self._lock is not None and self._shared_data is not None:
            with self._lock:
                job_list = self._shared_data['jobs']
                for i, job_dict in enumerate(job_list):
                    if job_dict['id'] == job_id:
                        del job_list[i]
                        log.debug(f"JobsProgress | Removed job: {job_dict['id']}")
                        break
        elif not self._use_multiprocessing and self._fallback_lock is not None:
            with self._fallback_lock:
                for i, job_dict in enumerate(self._fallback_jobs):
                    if job_dict['id'] == job_id:
                        del self._fallback_jobs[i]
                        log.debug(f"JobsProgress | Removed job: {job_dict['id']}")
                        break

    def get_job_list(self) -> Optional[str]:
        """
        Returns the list of job IDs as comma-separated string.
        """
        job_list = []
        if self._use_multiprocessing and self._lock is not None and self._shared_data is not None:
            with self._lock:
                job_list = list(self._shared_data['jobs'])
        elif not self._use_multiprocessing and self._fallback_lock is not None:
            with self._fallback_lock:
                job_list = list(self._fallback_jobs)
        
        if not job_list:
            return None

        log.debug(f"JobsProgress | Jobs in progress: {job_list}")
        return ",".join(str(job_dict['id']) for job_dict in job_list)

    def get_job_count(self) -> int:
        """
        Returns the number of jobs.
        """
        if self._use_multiprocessing and self._lock is not None and self._shared_data is not None:
            with self._lock:
                return len(self._shared_data['jobs'])
        elif not self._use_multiprocessing and self._fallback_lock is not None:
            with self._fallback_lock:
                return len(self._fallback_jobs)
        return 0

    def __iter__(self):
        """Make the class iterable - returns Job objects"""
        job_dicts = []
        if self._use_multiprocessing and self._lock is not None and self._shared_data is not None:
            with self._lock:
                job_dicts = list(self._shared_data['jobs'])
        elif not self._use_multiprocessing and self._fallback_lock is not None:
            with self._fallback_lock:
                job_dicts = list(self._fallback_jobs)
        return iter(Job(**job_dict) for job_dict in job_dicts)

    def __len__(self):
        """Support len() operation"""
        return self.get_job_count()

    def __contains__(self, element: Any) -> bool:
        """Support 'in' operator"""
        if isinstance(element, str):
            search_id = element
        elif isinstance(element, Job):
            search_id = element.id
        elif isinstance(element, dict):
            search_id = element.get('id')
        else:
            return False

        if self._use_multiprocessing and self._lock is not None and self._shared_data is not None:
            with self._lock:
                return any(job['id'] == search_id for job in self._shared_data['jobs'])
        elif not self._use_multiprocessing and self._fallback_lock is not None:
            with self._fallback_lock:
                return any(job['id'] == search_id for job in self._fallback_jobs)
        return False
