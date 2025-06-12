"""
Handles getting stuff from environment variables and updating the global state like job id.
"""

import os
import time
import uuid
from multiprocessing import Manager
from multiprocessing.managers import SyncManager
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
class JobsProgress:
    """Track the state of current jobs in progress using shared memory."""
    
    _instance: Optional['JobsProgress'] = None
    _manager: SyncManager
    _shared_data: Any
    _lock: Any

    def __new__(cls):
        if cls._instance is None:
            instance = object.__new__(cls)
            # Initialize instance variables
            instance._manager = Manager()
            instance._shared_data = instance._manager.dict()
            instance._shared_data['jobs'] = instance._manager.list()
            instance._lock = instance._manager.Lock()
            cls._instance = instance
        return cls._instance

    def __init__(self):
        # Everything is already initialized in __new__
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>: {self.get_job_list()}"

    def clear(self) -> None:
        with self._lock:
            self._shared_data['jobs'][:] = []

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

        with self._lock:
            # Check if job already exists
            job_list = self._shared_data['jobs']
            for existing_job in job_list:
                if existing_job['id'] == job_dict['id']:
                    return  # Job already exists
            
            # Add new job
            job_list.append(job_dict)
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

        with self._lock:
            for job_dict in self._shared_data['jobs']:
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

        with self._lock:
            job_list = self._shared_data['jobs']
            # Find and remove the job
            for i, job_dict in enumerate(job_list):
                if job_dict['id'] == job_id:
                    del job_list[i]
                    log.debug(f"JobsProgress | Removed job: {job_dict['id']}")
                    break

    def get_job_list(self) -> Optional[str]:
        """
        Returns the list of job IDs as comma-separated string.
        """
        with self._lock:
            job_list = list(self._shared_data['jobs'])
        
        if not job_list:
            return None

        log.debug(f"JobsProgress | Jobs in progress: {job_list}")
        return ",".join(str(job_dict['id']) for job_dict in job_list)

    def get_job_count(self) -> int:
        """
        Returns the number of jobs.
        """
        with self._lock:
            return len(self._shared_data['jobs'])

    def __iter__(self):
        """Make the class iterable - returns Job objects"""
        with self._lock:
            # Create a snapshot of jobs to avoid holding lock during iteration
            job_dicts = list(self._shared_data['jobs'])
        
        # Return an iterator of Job objects
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

        with self._lock:
            for job_dict in self._shared_data['jobs']:
                if job_dict['id'] == search_id:
                    return True
        return False
