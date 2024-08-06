'''
runpod | serverless | rp_scale.py
Provides the functionality for scaling the runpod serverless worker.
'''

import asyncio
import warnings
from runpod.serverless.modules.rp_logger import RunPodLogger
from .rp_job import get_job
from .worker_state import Jobs

log = RunPodLogger()
job_list = Jobs()


class JobScaler():
    """
    Job Scaler. This class is responsible for scaling the number of concurrent requests.
    """

    def __init__(self, concurrency_modifier = None):
        if concurrency_modifier:
            warnings.warn(
                "JobScaler(concurrency_modifier) is deprecated ",
                "and will be removed in a future version. "
                "Please remove `concurrency_modifier` parameter.",
                DeprecationWarning,
                stacklevel=2
            )
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
            log.debug(f"Jobs in progress: {job_list.get_job_count()}")

            tasks = [
                asyncio.create_task(get_job(session, retry=False))
            ]

            for job_future in asyncio.as_completed(tasks):
                if job := await job_future:
                    yield job
