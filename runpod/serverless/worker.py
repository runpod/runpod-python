"""
runpod | serverless | worker_loop.py
Called to convert a container into a worker pod for the runpod serverless platform.
"""
import os
import asyncio
from typing import Dict, Any

import aiohttp

from runpod.serverless.modules.rp_logger import RunPodLogger
from runpod.serverless.modules.rp_scale import JobScaler
from .modules import rp_local
from .modules.rp_handler import is_generator
from .modules.rp_ping import Heartbeat
from .modules.rp_job import run_job, run_job_generator
from .modules.rp_http import send_result, stream_result
from .modules.worker_state import REF_COUNT_ZERO, Jobs
from .utils import rp_debugger

log = RunPodLogger()
job_list = Jobs()
heartbeat = Heartbeat()

_TIMEOUT = aiohttp.ClientTimeout(total=300, connect=2, sock_connect=2)


def _get_auth_header() -> Dict[str, str]:
    '''
    Returns the authorization header for the worker HTTP requests.
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


async def _process_job(job, session, job_scaler, config):
    if is_generator(config["handler"]):
        generator_output = run_job_generator(config["handler"], job)
        log.debug("Handler is a generator, streaming results.")

        job_result = {'output': []}
        async for stream_output in generator_output:
            if 'error' in stream_output:
                job_result = stream_output
                break
            if config.get('return_aggregate_stream', False):
                job_result['output'].append(stream_output['output'])
            await stream_result(session, stream_output, job)
    else:
        job_result = await run_job(config["handler"], job)

    # If refresh_worker is set, pod will be reset after job is complete.
    if config.get("refresh_worker", False):
        log.info("refresh_worker flag set, stopping pod after job.", job['id'])
        job_result["stopPod"] = True
        job_scaler.kill_worker()

    # If rp_debugger is set, debugger output will be returned.
    if config["rp_args"].get("rp_debugger", False) and isinstance(job_result, dict):
        job_result["output"]["rp_debugger"] = rp_debugger.get_debugger_output()
        log.debug("rp_debugger | Flag set, returning debugger output.")

        # Calculate ready delay for the debugger output.
        ready_delay = (config["reference_counter_start"] - REF_COUNT_ZERO) * 1000
        job_result["output"]["rp_debugger"]["ready_delay_ms"] = ready_delay
    else:
        log.debug("rp_debugger | Flag not set, skipping debugger output.")
        rp_debugger.clear_debugger_output()

    # Send the job result to SLS
    await send_result(session, job_result, job)


# ------------------------- Main Worker Running Loop ------------------------- #
async def run_worker(config: Dict[str, Any]) -> None:
    """
    Starts the worker loop for multi-processing.

    Args:
        config (Dict[str, Any]): Configuration parameters for the worker.
    """
    heartbeat.start_ping()

    client_session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=None),
        headers=_get_auth_header(), timeout=_TIMEOUT
    )

    async with client_session as session:
        job_scaler = JobScaler(
            concurrency_controller=config.get('concurrency_controller', None)
        )

        while job_scaler.is_alive():

            async for job in job_scaler.get_jobs(session):
                # Process the job here
                task = asyncio.create_task(_process_job(job, session, job_scaler, config))

                # Track the task
                job_scaler.track_task(task)

                # Allow job processing
                await asyncio.sleep(0)

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

        finally:
            work_loop.close()
