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
    A class for automatically retrieving new jobs from the server and processing them concurrently.

    Attributes:
        server_url (str): The URL of the server to retrieve jobs from.
        max_concurrent_jobs (int): The maximum number of jobs to process concurrently.
        upscale_factor (float): The factor by which to upscale the job retrieval rate.
        downscale_factor (float): The factor by which to downscale the job retrieval rate.

    Methods:
        get_jobs() -> List[Dict]:
            Retrieves multiple jobs from the server in parallel using concurrent get requests.

        upscale_rate() -> None:
            Upscales the job retrieval rate by increasing the number of concurrent get requests.

        downscale_rate() -> None:
            Downscales the job retrieval rate by reducing the number of concurrent get requests.

        rescale_request_rate() -> None:
            Rescales the job retrieval rate based on factors such as job queue availability
            and handler utilization.

    Usage example:
        job_scaler = JobScaler(config)

        # Retrieving multiple jobs in parallel
        jobs_list = job_scaler.get_jobs(session)

        # Upscaling the rate for faster job retrieval
        job_scaler.upscale_rate()

        # Downscaling the rate for more conservative job retrieval
        job_scaler.downscale_rate()

        # Rescaling based on the queue, availability, and other metrics
        job_scaler.rescale_request_rate()
    """

    def __init__(self, concurrency_modifier: typing.Any):
        if concurrency_modifier is None:
            self.concurrency_modifier = _default_concurrency_modifier
        else:
            self.concurrency_modifier = concurrency_modifier

        self.background_get_job_tasks = set()
        self.job_history = []
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

    def track_task(self, task):
        """
        Keep track of the task to avoid python garbage collection of the coroutine.
        """
        self.background_get_job_tasks.add(task)
        task.add_done_callback(self.background_get_job_tasks.discard)

    async def get_jobs(self, session):
        """
        Retrieve multiple jobs from the server in parallel using concurrent requests.

        Returns:
            List[Any]: A list of job data retrieved from the server.
        """
        while True:
            if not self.is_alive():
                break

            self.current_concurrency = self.concurrency_modifier(self.current_concurrency)

            tasks = [
                asyncio.create_task(get_job(session, retry=False))
                for _ in range(self.current_concurrency if job_list.get_job_list() else 1)
            ]

            for job_future in asyncio.as_completed(tasks):
                job = await job_future
                self.job_history.append(1 if job else 0)
                if job:
                    yield job

            await asyncio.sleep(1)

            # Show logs
            log.info(
                f"Concurrent Get Jobs | The number of concurrent get_jobs is "
                f"{self.current_concurrency}."
            )
