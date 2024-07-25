'''
Handles getting stuff from environment variables and updating the global state like job id.
'''

import os
import uuid
import time
from typing import Optional, Dict, Any, Union

REF_COUNT_ZERO = time.perf_counter()  # Used for benchmarking with the debugger.

WORKER_ID = os.environ.get('RUNPOD_POD_ID', str(uuid.uuid4()))


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
        job_id: str,
        job_input: Optional[Dict[str, Any]] = None,
        webhook: Optional[str] = None,
    ) -> None:
        self.id = job_id
        self.input = job_input
        self.webhook = webhook

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
class Jobs:
    ''' Track the state of current jobs.'''

    _instance = None
    jobs = set()

    def __new__(cls):
        if Jobs._instance is None:
            Jobs._instance = object.__new__(cls)
            Jobs._instance.jobs = set()
        return Jobs._instance

    def add_job(self, job_id, job_input=None, webhook=None):
        '''
        Adds a job to the list of jobs.
        '''
        self.jobs.add(Job(job_id, job_input, webhook))

    def remove_job(self, job_id):
        '''
        Removes a job from the list of jobs.
        '''
        self.jobs.remove(Job(job_id))

    def get_job(self, job_id) -> Optional[Union[dict, list, str, int, float, bool]]:
        '''
        Returns the job with the given id.
        Used within rp_fastapi.py for local testing.
        '''
        for job in self.jobs:
            if job.id == job_id:
                return job

        return None

    def get_job_list(self):
        '''
        Returns the list of jobs as a string.
        '''
        return ','.join(str(job) for job in self.jobs) if self.jobs else None

    def get_job_count(self):
        '''
        Returns the number of jobs.
        '''
        return len(self.jobs)
