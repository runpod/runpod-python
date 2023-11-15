'''
runpod | serverless | rp_scale.py
Provides the functionality for scaling the runpod serverless worker.
'''

import asyncio
import typing
import dataclasses

from aiohttp_retry import Dict

from runpod.serverless.modules.rp_logger import RunPodLogger
from .rp_job import get_job
from .worker_state import Jobs

log = RunPodLogger()
job_list = Jobs()


@dataclasses.dataclass
class ConcurrencyConfig:
    """ A class for storing the configuration for the concurrency controller. """
    min_concurrent_requests: int = 2
    max_concurrent_requests: int = 100
    concurrency_scale_factor: int = 4
    availability_ratio_threshold: float = 0.90


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

    def __init__(self, concurrency_controller: typing.Any, config: Dict[str, typing.Any]):
        self.concurrency_controller = concurrency_controller

        self.config = ConcurrencyConfig(
            min_concurrent_requests=config.get("min_concurrent_requests", 2),
            max_concurrent_requests=config.get("max_concurrent_requests", 100),
            concurrency_scale_factor=config.get("concurrency_scale_factor", 4),
            availability_ratio_threshold=config.get("availability_ratio_threshold", 0.90)
        )

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

            tasks = [
                asyncio.create_task(get_job(session, retry=False))
                for _ in range(self.current_concurrency if job_list.get_job_list() else 1)
            ]

            for job_future in asyncio.as_completed(tasks):
                job = await job_future
                self.job_history.append(1 if job else 0)
                if job:
                    yield job

            # During the single processing scenario, wait for the job to finish processing.
            if self.concurrency_controller is None:
                # Create a copy of the background job tasks list to keep references to the tasks.
                job_tasks_copy = self.background_get_job_tasks.copy()
                if job_tasks_copy:
                    # Wait for the job tasks to finish processing before continuing.
                    await asyncio.wait(job_tasks_copy)
                # Exit the loop after processing a single job (since the handler is fully utilized).
                await asyncio.sleep(1)
                break

            # We retrieve current_concurrency jobs per second.
            await asyncio.sleep(1)

            # Rescale the retrieval rate appropriately.
            self.rescale_request_rate()

            # Show logs
            log.info(
                f"Concurrent Get Jobs | The number of concurrent get_jobs is "
                f"{self.current_concurrency}."
            )

    def upscale_rate(self) -> None:
        """
        Upscale the job retrieval rate by adjusting the number of concurrent requests.

        This method increases the number of concurrent requests to the server,
        effectively retrieving more jobs per unit of time.
        """
        self.current_concurrency = min(
            self.current_concurrency *
            self.config.concurrency_scale_factor,
            self.config.max_concurrent_requests
        )

    def downscale_rate(self) -> None:
        """
        Downscale the job retrieval rate by adjusting the number of concurrent requests.

        This method decreases the number of concurrent requests to the server,
        effectively retrieving fewer jobs per unit of time.
        """
        self.current_concurrency = int(max(
            self.current_concurrency // self.config.concurrency_scale_factor,
            self.config.min_concurrent_requests
        ))

    def rescale_request_rate(self) -> None:
        """
        Scale up or down the rate at which we are handling jobs from SLS.
        """
        # Compute the availability ratio of the job queue.
        availability_ratio = sum(self.job_history) / len(self.job_history)

        # If our worker is fully utilized or the SLS queue is throttling, reduce the job query rate.
        if self.concurrency_controller() is True:
            log.debug("Reducing job query rate due to full worker utilization.")

            self.downscale_rate()
        elif availability_ratio < 1 / self.config.concurrency_scale_factor:
            log.debug(
                "Reducing job query rate due to low job queue availability.")

            self.downscale_rate()
        elif availability_ratio >= self.config.availability_ratio_threshold:
            log.debug(
                "Increasing job query rate due to increased job queue availability.")

            self.upscale_rate()

        # Clear the job history
        self.job_history.clear()
