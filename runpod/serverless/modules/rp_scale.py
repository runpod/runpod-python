'''
runpod | serverless | rp_scale.py
Provides the functionality for scaling the runpod serverless worker.
'''

import asyncio
from runpod.serverless.modules.rp_logger import RunPodLogger
from .rp_job import get_job
from .worker_state import JobsQueue, REF_COUNT_ZERO

log = RunPodLogger()
job_list = JobsQueue()


class JobScaler():
    """
    Job Scaler. This class is responsible for scaling the number of concurrent requests.
    """

    def __init__(self, concurrency_modifier = lambda x: x):
        self.concurrency_modifier = concurrency_modifier
        self.current_concurrency = 1
        self._is_alive = True

    def is_alive(self):
        """
        Return whether the worker is alive or not.
        """
        return self._is_alive

    def kill_worker(self):
        """
        Whether to kill the worker.
        """
        self._is_alive = False

    async def get_jobs(self, session):
        """
        Retrieve multiple jobs from the server in parallel using concurrent requests.

        Returns:
            List[Any]: A list of job data retrieved from the server.
        """
        while self.is_alive():
            self.current_concurrency = self.concurrency_modifier(self.current_concurrency)
            log.debug(f"Concurrency set to: {self.current_concurrency}")

            log.debug(f"Jobs in progress: {job_list.get_job_count()}")

            jobs_needed = self.current_concurrency - job_list.get_job_count()

            acquire_jobs = await asyncio.create_task(get_job(session, jobs_needed, retry=False))

            if acquire_jobs:
                for job in acquire_jobs:
                    yield job

            await asyncio.sleep(0)
