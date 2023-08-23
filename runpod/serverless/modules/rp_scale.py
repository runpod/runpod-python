'''
runpod | serverless | rp_scale.py
Provides the functionality for scaling the runpod serverless worker.
'''

import asyncio
import threading
import typing
import os
import aiohttp
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
    CONCURRENCY_SCALE_FACTOR = 2.0
    AVAILABILITY_RATIO_THRESHOLD = 0.90
    INITIAL_CONCURRENT_REQUESTS = 1
    MAX_CONCURRENT_REQUESTS = 100
    MIN_CONCURRENT_REQUESTS = 1
    SLEEP_INTERVAL_SEC = 0.30

    def __init__(self, concurrency_controller: typing.Any = None):
        self.background_get_job_tasks = set()
        self.num_concurrent_get_job_requests = JobScaler.INITIAL_CONCURRENT_REQUESTS
        self.job_history = []
        self.concurrency_controller = concurrency_controller
        self._is_alive = True
        self.queue = []

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

    def start(self):
        """
        empty
        """
        loop = asyncio.new_event_loop()
        threading.Thread(
            target=lambda: loop.run_until_complete(self.get_jobs()),
            daemon=True
        ).start()

    def get_from_queue(self):
        """
        Retrieve jobs from the job take scaler queue.
        """
        # Retrieve a snapshot of the queue.
        snapshot = self.queue.copy()

        # Clear the snapshot from the queue.
        self.queue[0:len(snapshot)] = []

        return snapshot

    async def get_jobs(self):
        """
        Retrieve multiple jobs from the server in parallel using concurrent requests.

        Returns:
            List[Any]: A list of job data retrieved from the server.
        """
        # A session needs to be instantiated within the 'get_jobs' coroutine.
        timeout = aiohttp.ClientTimeout(total=300, connect=2, sock_connect=2)
        connector = aiohttp.TCPConnector(limit=None, limit_per_host=None)
        session = aiohttp.ClientSession(
            connector=connector,
            headers={
                "Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"},
            timeout=timeout
        )

        while self.is_alive():
            # Employ parallel processing if there are jobs in progress.
            use_parallel_processing = job_list.get_job_list() is not None

            # We intend to maintain the 'jobs_in_progress' value constant throughout the entire
            # parallel processing flow below to prevent a race condition within SLS.
            # If the 'jobs_in_progress' count is 0, we should proceed sequentially.
            # Otherwise, we should proceed in parallel.
            if use_parallel_processing:
                # Prepare the 'get_job' tasks for parallel execution.
                tasks = [
                    asyncio.create_task(
                        get_job(session, force_in_progress=True, retry=False)
                    )
                    for _ in range(self.num_concurrent_get_job_requests)
                ]

                # Wait for all the 'get_job' tasks, which are running in parallel, to be completed.
                for job_future in asyncio.as_completed(tasks):
                    job = await job_future
                    self.job_history.append(1 if job else 0)

                    if job:
                        self.queue.append(job)
            else:
                for _ in range(self.num_concurrent_get_job_requests):
                    # The latency for get_job is 0.3 seconds.
                    job = await get_job(session, retry=False)
                    self.job_history.append(1 if job else 0)

                    if job:
                        self.queue.append(job)

            # In the scenario involving a single processing worker, we employ a variant of the
            # concurrency_controller in which we wait or delay until the tasks have been fully
            # completed. For instance, it is plausible to encounter a worker handling CPU-intensive
            # workloads. In such workloads, tasks may range from completing within 10ms to 100ms on
            # a single worker. Therefore, it becomes logical to enqueue multiple jobs simultaneously
            # within the JobScaler to manage these workloads effectively.
            if self.concurrency_controller is None:
                # Create a copy of the background job tasks list to keep references to the tasks.
                # Wait for the job tasks to finish processing before continuing.
                # Note: asyncio.wait requires a non-empty list or it throws an exception.
                jobs = self.background_get_job_tasks.copy()
                if jobs:
                    await asyncio.wait(jobs)
                break

            # Show logs
            log.info(
                f"Concurrent Get Jobs | The number of concurrent get_jobs is "
                f"{self.num_concurrent_get_job_requests}."
                "The number of yielded jobs is "
                f"{sum(self.job_history)} of {len(self.job_history)}."
            )

            # Adjust the job retrieval rate by considering factors such as queue availability
            # and the pace at which we are processing jobs within the multi-processing worker.
            self.rescale_request_rate()

            # In the parallel processing scenario, we intend to sleep for a certain number of
            # seconds at each interval. However, in the case of sequential processing, this results
            # in excessive overhead, which we strive to avoid.
            if use_parallel_processing:
                await asyncio.sleep(JobScaler.SLEEP_INTERVAL_SEC)

    def upscale_rate(self) -> None:
        """
        Upscale the job retrieval rate by adjusting the number of concurrent requests.

        This method increases the number of concurrent requests to the server,
        effectively retrieving more jobs per unit of time.
        """
        self.num_concurrent_get_job_requests = int(min(
            self.num_concurrent_get_job_requests *
            JobScaler.CONCURRENCY_SCALE_FACTOR,
            JobScaler.MAX_CONCURRENT_REQUESTS
        ))

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
        availability_ratio = sum(self.job_history) / \
            (len(self.job_history) + 0.0)

        # If our worker is fully utilized or the SLS queue is throttling, reduce the job query rate.
        if self.concurrency_controller and self.concurrency_controller() is True:
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
