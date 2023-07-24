"""
runpod | serverless | worker_loop.py
Called to convert a container into a worker pod for the runpod serverless platform.
"""

import os
import asyncio
import inspect
from typing import Dict, Any

import aiohttp

from runpod.serverless.modules.rp_logger import RunPodLogger
from .modules import rp_local
from .modules.rp_ping import HeartbeatSender
from .modules.rp_job import get_job, run_job, run_job_generator
from .modules.rp_http import send_result, stream_result
from .modules.worker_state import REF_COUNT_ZERO, Jobs
from .utils import rp_debugger

log = RunPodLogger()
job_list = Jobs()

_TIMEOUT = aiohttp.ClientTimeout(total=300, connect=2, sock_connect=2)

heartbeat = HeartbeatSender()


def _get_auth_header() -> Dict[str, str]:
    '''
    Returns the authorization header for the worker.
    '''
    return {"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"}


def _is_local(config) -> bool:
    '''
    Returns True if the environment variable RUNPOD_WEBHOOK_GET_JOB is not set.
    '''
    if config['rp_args'].get('test_input', None):
        return True

    if os.environ.get("RUNPOD_WEBHOOK_GET_JOB", None) is None:
        return True

    return False


class JobProcessor():
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
        job_processor = JobProcessor(config)

        # Retrieving multiple jobs in parallel
        jobs_list = job_processor.get_jobs(session)

        # Upscaling the rate for faster job retrieval
        job_processor.upscale_rate()

        # Downscaling the rate for more conservative job retrieval
        job_processor.downscale_rate()

        # Rescaling based on the queue, availability, and other metrics
        job_processor.rescale_request_rate()
    """

    # Scaling Constants
    CONCURRENCY_SCALE_FACTOR = 2
    AVAILABILITY_RATIO_THRESHOLD = 0.90
    INITIAL_CONCURRENT_REQUESTS = 1
    MAX_CONCURRENT_REQUESTS = 100
    MIN_CONCURRENT_REQUESTS = 1

    def __init__(self, handler_fully_utilized):
        self.background_get_job_tasks = set()
        self.num_concurrent_get_job_requests = JobProcessor.INITIAL_CONCURRENT_REQUESTS
        self.job_history = []
        self.handler_fully_utilized = handler_fully_utilized
        self.is_alive = True

    def kill_worker(self):
        """
        Whether to kill the worker.
        """
        self.is_alive = False

    async def get_jobs(self, session):
        """
        Retrieve multiple jobs from the server in parallel using concurrent requests.

        Returns:
            List[Any]: A list of job data retrieved from the server.
        """
        while True:
            tasks = [
                asyncio.create_task(
                    get_job(session, retry=False)
                ) for _ in range(self.num_concurrent_get_job_requests)]

            for job_future in asyncio.as_completed(tasks):
                job = await job_future
                self.job_history.append(1 if job else 0)

                # Add the job to our list of jobs
                if job:
                    job_list.add_job(job["id"])
                    log.debug(f"{job['id']} | Set Job ID")
                    yield job

            # During the single processing scenario, wait for the job to finish processing.
            if not self.handler_fully_utilized:
                await asyncio.wait(self.background_get_job_tasks)
                break

            # We retrieve num_concurrent_get_job_requests jobs per second.
            await asyncio.sleep(1)

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
            JobProcessor.CONCURRENCY_SCALE_FACTOR,
            JobProcessor.MAX_CONCURRENT_REQUESTS
        )

    def downscale_rate(self) -> None:
        """
        Downscale the job retrieval rate by adjusting the number of concurrent requests.

        This method decreases the number of concurrent requests to the server,
        effectively retrieving fewer jobs per unit of time.
        """
        self.num_concurrent_get_job_requests = int(max(
            self.num_concurrent_get_job_requests // JobProcessor.CONCURRENCY_SCALE_FACTOR,
            JobProcessor.MIN_CONCURRENT_REQUESTS
        ))

    def rescale_request_rate(self) -> None:
        """
        Scale up or down the rate at which we are handling jobs from SLS.
        """
        # If we're inside the single-processing setting.
        if not self.handler_fully_utilized:
            return

        # There's no job history for our rescaling mechanism.
        if len(self.job_history) == 0:
            return

        # Compute the availability ratio of the job queue.
        availability_ratio = sum(self.job_history) / len(self.job_history)

        # If our worker is fully utilized or the SLS queue is throttling, reduce the job query rate.
        if self.handler_fully_utilized() is True:
            log.debug("Reducing job query rate due to full worker utilization.")

            self.downscale_rate()
        elif availability_ratio < 1 / JobProcessor.CONCURRENCY_SCALE_FACTOR:
            log.debug("Reducing job query rate due to low job queue availability.")

            self.downscale_rate()
        elif availability_ratio >= JobProcessor.AVAILABILITY_RATIO_THRESHOLD:
            log.debug("Increasing job query rate due to increased job queue availability.")

            self.upscale_rate()

        # Clear the job history
        self.job_history.clear()


# ------------------------- Main Worker Running Loop ------------------------- #
async def run_worker(config: Dict[str, Any]) -> None:
    """
    Starts the worker loop for multi-processing.

    Args:
        config (Dict[str, Any]): Configuration parameters for the worker.
    """
    auth_header = _get_auth_header()
    connector = aiohttp.TCPConnector(limit=None)

    job_processor = JobProcessor(
        handler_fully_utilized=config.get('handler_fully_utilized')
    )

    async with aiohttp.ClientSession(
            connector=connector, headers=auth_header, timeout=_TIMEOUT) as session:

        heartbeat.start_ping()

        # Flag to kill the worker after job is complete.
        while job_processor.is_alive:
            async def process_job(job):
                if inspect.isgeneratorfunction(config["handler"]):
                    job_result = run_job_generator(config["handler"], job)

                    log.debug("Handler is a generator, streaming results.")
                    async for job_stream in job_result:
                        await stream_result(session, job_stream, job)
                    job_result = {}
                else:
                    job_result = await run_job(config["handler"], job)

                # If refresh_worker is set, pod will be reset after job is complete.
                if config.get("refresh_worker", False):
                    log.info(
                        f"refresh_worker | Flag set, stopping pod after job {job['id']}.")
                    job_result["stopPod"] = True
                    job_processor.kill_worker()

                # If rp_debugger is set, debugger output will be returned.
                if config["rp_args"].get("rp_debugger", False) and isinstance(job_result, dict):
                    log.debug("rp_debugger | Flag set, return debugger output.")
                    job_result["output"]["rp_debugger"] = rp_debugger.get_debugger_output()

                    # Calculate ready delay for the debugger output.
                    ready_delay = (
                        config["reference_counter_start"] - REF_COUNT_ZERO) * 1000
                    job_result["output"]["rp_debugger"]["ready_delay_ms"] = ready_delay
                else:
                    log.debug(
                        "rp_debugger | Flag not set, skipping debugger output.")

                    rp_debugger.clear_debugger_output()

                # Send the job result to SLS
                await send_result(session, job_result, job)

                log.info(f'{job["id"]} | Finished')
                job_list.remove_job(job["id"])

            async for job in job_processor.get_jobs(session):
                # Process the job here
                task = asyncio.create_task(process_job(job))
                job_processor.background_get_job_tasks.add(task)
                task.add_done_callback(
                    job_processor.background_get_job_tasks.discard)

        # Stops the worker loop if the kill_worker flag is set.
        asyncio.get_event_loop().stop()


def main(config: Dict[str, Any]) -> None:
    """
    Checks if the worker is running locally or on RunPod.
    If running locally, the test job is run and the worker exits.
    If running on RunPod, the worker loop is created.
    """
    if _is_local(config):
        asyncio.run(rp_local.run_local(config))
    else:
        try:
            work_loop = asyncio.new_event_loop()
            asyncio.ensure_future(run_worker(config), loop=work_loop)
            work_loop.run_forever()

        except Exception as exception:  # pylint: disable=broad-exception-caught
            log.debug(
                f"rp_debugger | The event loop has closed due to {exception}.")
        finally:
            work_loop.close()
