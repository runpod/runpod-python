'''
Handles getting stuff from environment variables and updating the global state like job id.
'''

import os
import uuid
import time

REF_COUNT_ZERO = time.perf_counter()  # Used for benchmarking with the debugger.

WORKER_ID = os.environ.get('RUNPOD_POD_ID', str(uuid.uuid4()))


# ----------------------------------- Flags ---------------------------------- #
IS_LOCAL_TEST = os.environ.get("RUNPOD_WEBHOOK_GET_JOB", None) is None


def get_auth_header():
    '''
    Returns the authorization header with the API key.
    '''
    return {"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"}



class Jobs:
    ''' Track the state of current jobs.'''

    _instance = None
    jobs = set()

    def __new__(cls):
        if Jobs._instance is None:
            Jobs._instance = object.__new__(cls)
            Jobs._instance.jobs = set()
        return Jobs._instance

    def add_job(self, job_id):
        '''
        Adds a job to the list of jobs.
        '''
        self.jobs.add(job_id)

    def remove_job(self, job_id):
        '''
        Removes a job from the list of jobs.
        '''
        self.jobs.remove(job_id)

    def get_job_list(self):
        '''
        Returns the list of jobs as a string.
        '''
        if len(self.jobs) == 0:
            return None

        return ','.join(list(self.jobs))
