'''
runpod | serverless | rp_scale.py
Provides the functionality for scaling the runpod serverless worker.
'''

import asyncio
import typing
from runpod.serverless.modules.rp_logger import RunPodLogger
from .rp_job import get_job
from .worker_state import Jobs

log = RunPodLogger()
job_list = Jobs()


def _default_concurrency_modifier(current_concurrency: int) -> int:
    """
    Default concurrency modifier.
    This function returns the current concurrency without any modification.
    Args:
        current_concurrency (int): The current concurrency.
    Returns:
        int: The current concurrency.
    """
    return current_concurrency


class JobScaler():
    """
    Job Scaler. This class is responsible for scaling the number of concurrent requests.
    """

    def __init__(self, concurrency_modifier: typing.Any):
        if concurrency_modifier is None:
            self.concurrency_modifier = _default_concurrency_modifier
        else:
            self.concurrency_modifier = concurrency_modifier

        self.current_concurrency = 1

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

        Yields:
            Dict[str, Any]: A job data retrieved from the server.
        """
        while self.is_alive():
            self.current_concurrency = self.concurrency_modifier(self.current_concurrency)
            log.debug(f"Concurrency set to: {self.current_concurrency}")

            job_count = job_list.get_job_count()
            log.debug(f"Jobs in progress: {job_count}")

            if job_count < self.current_concurrency:
                log.debug("Job list is less than concurrency, getting more jobs.")
                # TODO: use the new batch job take
                tasks = [
                    asyncio.create_task(get_job(session, retry=False))
                    for _ in range(self.current_concurrency - job_count)
                ]

                for job_future in asyncio.as_completed(tasks):
                    # TODO: yield the batched jobs returned
                    job = await job_future
                    if job:
                        yield job

            await asyncio.sleep(0)
