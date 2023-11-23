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
            Retrieves multiple jobs from the server in parallel using concurrent requests.

        upscale_rate() -> None:
            Upscales the job retrieval rate by adjusting the number of concurrent requests.

        downscale_rate() -> None:
            Downscales the job retrieval rate by adjusting the number of concurrent requests.

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

    # Scaling Constants
    CONCURRENCY_SCALE_FACTOR = 2
    AVAILABILITY_RATIO_THRESHOLD = 0.90
    INITIAL_CONCURRENT_REQUESTS = 1
    MAX_CONCURRENT_REQUESTS = 100
    MIN_CONCURRENT_REQUESTS = 1
    SLEEP_INTERVAL_SEC = 1

    def __init__(self, concurrency_controller: typing.Any):
        self.background_get_job_tasks = set()
        self.num_concurrent_get_job_requests = JobScaler.INITIAL_CONCURRENT_REQUESTS
        self.job_history = []
        self.concurrency_controller = concurrency_controller
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
            # Finish if the job_scale is not alive
            if not self.is_alive():
                break

            for _ in range(self.num_concurrent_get_job_requests):
                job = await get_job(session)
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
                await asyncio.sleep(JobScaler.SLEEP_INTERVAL_SEC)
                break

            # We retrieve num_concurrent_get_job_requests jobs per second.
            await asyncio.sleep(JobScaler.SLEEP_INTERVAL_SEC)

            # Rescale the retrieval rate appropriately.
            self.rescale_request_rate()

            # Show logs
            log.info(
                f"Concurrent Get Jobs | The number of concurrent get_jobs is "
                f"{self.num_concurrent_get_job_requests}."
            )

    def upscale_rate(self) -> None:
        """
        Upscale the job retrieval rate by adjusting the number of concurrent requests.

        This method increases the number of concurrent requests to the server,
        effectively retrieving more jobs per unit of time.
        """
        self.num_concurrent_get_job_requests = min(
            self.num_concurrent_get_job_requests *
            JobScaler.CONCURRENCY_SCALE_FACTOR,
            JobScaler.MAX_CONCURRENT_REQUESTS
        )

    def downscale_rate(self) -> None:
        """
        Downscale the job retrieval rate by adjusting the number of concurrent requests.

        This method decreases the number of concurrent requests to the server,
        effectively retrieving fewer jobs per unit of time.
        """
        self.num_concurrent_get_job_requests = int(max(
            self.num_concurrent_get_job_requests // JobScaler.CONCURRENCY_SCALE_FACTOR,
            JobScaler.MIN_CONCURRENT_REQUESTS
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
        elif availability_ratio < 1 / JobScaler.CONCURRENCY_SCALE_FACTOR:
            log.debug(
                "Reducing job query rate due to low job queue availability.")

            self.downscale_rate()
        elif availability_ratio >= JobScaler.AVAILABILITY_RATIO_THRESHOLD:
            log.debug(
                "Increasing job query rate due to increased job queue availability.")

            self.upscale_rate()

        # Clear the job history
        self.job_history.clear()
