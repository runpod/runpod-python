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


# Constants
SCALE_FACTOR = 2
INITIAL_CONCURRENT_REQUESTS = 1
MAX_CONCURRENT_REQUESTS = 10000 #sys.maxsize
MIN_CONCURRENT_REQUESTS = 1
AVAILABILITY_RATIO_THRESHOLD = 0.90

class Scaler():
    def __init__(self, handler_fully_utilized):
        self.background_tasks = set()
        self.num_concurrent_requests = INITIAL_CONCURRENT_REQUESTS
        self.job_history = []  # Keeps track of recent job results
        self.handler_fully_utilized = handler_fully_utilized

    async def get_jobs(self, session):
        while True:
            tasks = [asyncio.create_task(get_job(session)) for _ in range(self.num_concurrent_requests)]
            for job_future in asyncio.as_completed(tasks):
                job = await job_future
                self.job_history.append(job)

                if job:
                    job_list.add_job(job["id"])
                    log.debug(f"{job['id']} | Set Job ID")
                    yield job

            await asyncio.sleep(1)

            self.rescale_request_rate()

            log.info(f"Concurrent Get Jobs | The number of concurrent get_jobs is {self.num_concurrent_requests}.")

    # Scale up or down the rate at which we are handling jobs from SLS.
    def rescale_request_rate(self, force_downscale=False):
        if force_downscale:
            self.num_concurrent_requests = int(max(
                self.num_concurrent_requests // SCALE_FACTOR, MIN_CONCURRENT_REQUESTS))
            return

        if len(self.job_history) < 10:
            return

        # Compute the availability ratio of the job queue.
        none_jobs_count = sum(job is None for job in self.job_history)
        availability_ratio = 1 - none_jobs_count / len(self.job_history)

        # If our worker is fully utilized or the SLS queue is throttling, reduce the job query rate.
        if self.handler_fully_utilized() is True:
            # Reduce job query rate.
            self.num_concurrent_requests = int(max(
                self.num_concurrent_requests // SCALE_FACTOR, MIN_CONCURRENT_REQUESTS))
        elif availability_ratio < 1 / SCALE_FACTOR:
            # Reduce job query rate.
            self.num_concurrent_requests = int(max(
                self.num_concurrent_requests // SCALE_FACTOR, MIN_CONCURRENT_REQUESTS))
        elif availability_ratio >= AVAILABILITY_RATIO_THRESHOLD:
            # Assess if SLS queue has enough jobs to scale job query rate.
            # Increase concurrent request count.
            self.num_concurrent_requests = min(
                self.num_concurrent_requests * SCALE_FACTOR, MAX_CONCURRENT_REQUESTS
            )

        # Clear the job history
        self.job_history.clear()


# ------------------------- Main Worker Running Loop ------------------------- #
async def run_worker_multi(config: Dict[str, Any]) -> None:
    """
    Starts the worker loop for multi-processing.
    """
    auth_header = _get_auth_header()
    connector = aiohttp.TCPConnector(limit=None)
    scalar = Scaler(config.get('handler_fully_utilized'))
    async with aiohttp.ClientSession(connector=connector, headers=auth_header, timeout=_TIMEOUT) as session:

        heartbeat.start_ping()

        kill_worker = False # Flag to kill the worker after job is complete.
        while kill_worker is False:
            async def process_job(job):
                if inspect.isgeneratorfunction(config["handler"]):
                    job_result = await run_job_generator(config["handler"], job)

                    log.debug("Handler is a generator, streaming results.")
                    for job_stream in job_result:
                        await stream_result(session, job_stream, job)
                    job_result = {}
                else:
                    job_result = await run_job(config["handler"], job)

                # If refresh_worker is set, pod will be reset after job is complete.
                if config.get("refresh_worker", False):
                    log.info(f"refresh_worker | Flag set, stopping pod after job {job['id']}.")
                    job_result["stopPod"] = True
                    global kill_worker
                    kill_worker = True

                # If rp_debugger is set, debugger output will be returned.
                if config["rp_args"].get("rp_debugger", False) and isinstance(job_result, dict):
                    log.debug("rp_debugger | Flag set, return debugger output.")
                    job_result["output"]["rp_debugger"] = rp_debugger.get_debugger_output()

                    ready_delay = (config["reference_counter_start"] - REF_COUNT_ZERO) * 1000
                    job_result["output"]["rp_debugger"]["ready_delay_ms"] = ready_delay
                else:
                    log.debug("rp_debugger | Flag not set, skipping debugger output.")
                    rp_debugger.clear_debugger_output()

                await send_result(session, job_result, job)

                log.info(f'{job["id"]} | Finished')
                job_list.remove_job(job["id"])

            # Create process job task
            async for job in scalar.get_jobs(session):
                # Process the job here
                task = asyncio.create_task(process_job(job))
                scalar.background_tasks.add(task)
                task.add_done_callback(scalar.background_tasks.discard)

        asyncio.get_event_loop().stop() # Stops the worker loop if the kill_worker flag is set.


async def run_worker(config: Dict[str, Any]) -> None:
    """
    Starts the worker loop.
    """
    auth_header = _get_auth_header()
    async with aiohttp.ClientSession(headers=auth_header, timeout=_TIMEOUT) as session:

        heartbeat.start_ping()

        kill_worker = False # Flag to kill the worker after job is complete.
        while kill_worker is False:
            job = await get_job(session)

            job_list.add_job(job["id"])
            log.debug(f"{job['id']} | Set Job ID")

            if inspect.isgeneratorfunction(config["handler"]):
                job_result = await run_job_generator(config["handler"], job)

                log.debug("Handler is a generator, streaming results.")
                for job_stream in job_result:
                    await stream_result(session, job_stream, job)
                job_result = {}
            else:
                job_result = await run_job(config["handler"], job)

            # If refresh_worker is set, pod will be reset after job is complete.
            if config.get("refresh_worker", False):
                log.info(f"refresh_worker | Flag set, stopping pod after job {job['id']}.")
                job_result["stopPod"] = True
                kill_worker = True

            # If rp_debugger is set, debugger output will be returned.
            if config["rp_args"].get("rp_debugger", False) and isinstance(job_result, dict):
                log.debug("rp_debugger | Flag set, return debugger output.")
                job_result["output"]["rp_debugger"] = rp_debugger.get_debugger_output()

                ready_delay = (config["reference_counter_start"] - REF_COUNT_ZERO) * 1000
                job_result["output"]["rp_debugger"]["ready_delay_ms"] = ready_delay
            else:
                log.debug("rp_debugger | Flag not set, skipping debugger output.")
                rp_debugger.clear_debugger_output()

            await send_result(session, job_result, job)

            log.info(f'{job["id"]} | Finished')
            job_list.remove_job(job["id"])

        asyncio.get_event_loop().stop() # Stops the worker loop if the kill_worker flag is set.


def main(config: Dict[str, Any]) -> None:
    """
    Checks if the worker is running locally or on RunPod.
    If running locally, the test job is run and the worker exits.
    If running on RunPod, the worker loop is created.
    """
    if _is_local(config):
        rp_local.run_local(config)

    else:
        try:
            work_loop = asyncio.new_event_loop()
            if config['handler_fully_utilized'] is not None:
                asyncio.ensure_future(run_worker_multi(config), loop=work_loop)
            else:
                asyncio.ensure_future(run_worker(config), loop=work_loop)
            work_loop.run_forever()

        finally:
            work_loop.close()
