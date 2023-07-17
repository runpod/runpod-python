"""
runpod | serverless | worker_loop.py
Called to convert a container into a worker pod for the runpod serverless platform.
"""

import os
import types
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


# ------------------------- Main Worker Running Loop ------------------------- #
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
                job_result = run_job_generator(config["handler"], job)

                log.debug("Handler is a generator, streaming results.")
                for job_stream in job_result:
                    await stream_result(session, job_stream, job)
                job_result = {}
            else:
                job_result = run_job(config["handler"], job)

            # If refresh_worker is set, pod will be reset after job is complete.
            if config.get("refresh_worker", False):
                log.info(f"refresh_worker | Flag set, stopping pod after job {job['id']}.")
                job_result["stopPod"] = True
                kill_worker = True

            # If rp_debugger is set, debugger output will be returned.
            if config["rp_args"].get("rp_debugger", False):
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
            asyncio.ensure_future(run_worker(config), loop=work_loop)
            work_loop.run_forever()

        finally:
            work_loop.close()
