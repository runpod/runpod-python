"""
Handles getting stuff from environment variables and updating the global state like job id.
"""

import os
import time
import uuid
import threading
from multiprocessing import Manager
from multiprocessing.managers import SyncManager
from typing import Any, Dict, Optional, List

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

class _JobStorage:
    """Abstract storage backend for jobs."""
    
    def add_job(self, job_dict: Dict[str, Any]) -> None:
        raise NotImplementedError
    
    def remove_job(self, job_id: str) -> None:
        raise NotImplementedError
        
    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError
        
    def get_all_jobs(self) -> List[Dict[str, Any]]:
        raise NotImplementedError
        
    def clear_jobs(self) -> None:
        raise NotImplementedError
    
    def job_exists(self, job_id: str) -> bool:
        raise NotImplementedError


class _MultiprocessingStorage(_JobStorage):
    """Multiprocessing-based storage for GIL-free operation."""
    
    def __init__(self):
        self._manager = Manager()
        self._shared_data = self._manager.dict()
        self._shared_data['jobs'] = self._manager.list()
        self._lock = self._manager.Lock()
        
    def add_job(self, job_dict: Dict[str, Any]) -> None:
        with self._lock:
            job_list = self._shared_data['jobs']
            if not any(job['id'] == job_dict['id'] for job in job_list):
                job_list.append(job_dict)
    
    def remove_job(self, job_id: str) -> None:
        with self._lock:
            job_list = self._shared_data['jobs']
            for i, job in enumerate(job_list):
                if job['id'] == job_id:
                    del job_list[i]
                    break
    
    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for job in self._shared_data['jobs']:
                if job['id'] == job_id:
                    return dict(job)
        return None
    
    def get_all_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(job) for job in self._shared_data['jobs']]
    
    def clear_jobs(self) -> None:
        with self._lock:
            self._shared_data['jobs'][:] = []
    
    def job_exists(self, job_id: str) -> bool:
        with self._lock:
            return any(job['id'] == job_id for job in self._shared_data['jobs'])


class _ThreadSafeStorage(_JobStorage):
    """Thread-safe storage fallback when multiprocessing fails."""
    
    def __init__(self):
        self._jobs: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
    
    def add_job(self, job_dict: Dict[str, Any]) -> None:
        with self._lock:
            if not any(job['id'] == job_dict['id'] for job in self._jobs):
                self._jobs.append(job_dict)
    
    def remove_job(self, job_id: str) -> None:
        with self._lock:
            for i, job in enumerate(self._jobs):
                if job['id'] == job_id:
                    del self._jobs[i]
                    break
    
    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for job in self._jobs:
                if job['id'] == job_id:
                    return job.copy()
        return None
    
    def get_all_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return self._jobs.copy()
    
    def clear_jobs(self) -> None:
        with self._lock:
            self._jobs.clear()
    
    def job_exists(self, job_id: str) -> bool:
        with self._lock:
            return any(job['id'] == job_id for job in self._jobs)


class JobsProgress:
    """Track the state of current jobs in progress using shared memory or thread-safe fallback."""
    
    _instance: Optional['JobsProgress'] = None
    _storage: Optional[_JobStorage] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = object.__new__(cls)
            cls._instance._storage = None
        return cls._instance

    def __init__(self):
        pass
    
    def _ensure_initialized(self):
        """Lazily initialize storage backend."""
        if self._storage is None:
            try:
                self._storage = _MultiprocessingStorage()
                log.debug("JobsProgress | Using multiprocessing for GIL-free operation")
            except Exception as e:
                log.warn(f"JobsProgress | Multiprocessing failed ({e}), falling back to thread-safe mode")
                self._storage = _ThreadSafeStorage()

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>: {self.get_job_list()}"

    def clear(self) -> None:
        self._ensure_initialized()
        self._storage.clear_jobs()

    def add(self, element: Any):
        """
        Adds a Job object to the set.
        """
        self._ensure_initialized()
        if isinstance(element, str):
            job_dict = {'id': element}
        elif isinstance(element, dict):
            job_dict = element
        elif hasattr(element, 'id'):
            job_dict = {'id': element.id}
        else:
            raise TypeError("Only Job objects can be added to JobsProgress.")

        self._storage.add_job(job_dict)
        log.debug(f"JobsProgress | Added job: {job_dict['id']}")

    def get(self, element: Any) -> Optional[Job]:
        """
        Retrieves a Job object from the set.
        
        If the element is a string, searches for Job with that id.
        """
        self._ensure_initialized()
        if isinstance(element, str):
            search_id = element
        elif isinstance(element, Job):
            search_id = element.id
        else:
            raise TypeError("Only Job objects can be retrieved from JobsProgress.")

        job_dict = self._storage.get_job(search_id)
        if job_dict:
            log.debug(f"JobsProgress | Retrieved job: {job_dict['id']}")
            return Job(**job_dict)
        return None

    def remove(self, element: Any):
        """
        Removes a Job object from the set.
        """
        self._ensure_initialized()
        if isinstance(element, str):
            job_id = element
        elif isinstance(element, dict):
            job_id = element.get('id')
        elif hasattr(element, 'id'):
            job_id = element.id
        else:
            raise TypeError("Only Job objects can be removed from JobsProgress.")

        self._storage.remove_job(job_id)
        log.debug(f"JobsProgress | Removed job: {job_id}")

    def get_job_list(self) -> Optional[str]:
        """
        Returns the list of job IDs as comma-separated string.
        """
        self._ensure_initialized()
        job_list = self._storage.get_all_jobs()
        
        if not job_list:
            return None

        log.debug(f"JobsProgress | Jobs in progress: {job_list}")
        return ",".join(str(job_dict['id']) for job_dict in job_list)

    def get_job_count(self) -> int:
        """
        Returns the number of jobs.
        """
        self._ensure_initialized()
        return len(self._storage.get_all_jobs())

    def __iter__(self):
        """Make the class iterable - returns Job objects"""
        self._ensure_initialized()
        job_dicts = self._storage.get_all_jobs()
        return iter(Job(**job_dict) for job_dict in job_dicts)

    def __len__(self):
        """Support len() operation"""
        return self.get_job_count()

    def __contains__(self, element: Any) -> bool:
        """Support 'in' operator"""
        self._ensure_initialized()
        if isinstance(element, str):
            search_id = element
        elif isinstance(element, Job):
            search_id = element.id
        elif isinstance(element, dict):
            search_id = element.get('id')
        else:
            return False

        return self._storage.job_exists(search_id)
