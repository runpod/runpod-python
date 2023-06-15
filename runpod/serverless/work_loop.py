"""
runpod | serverless | worker_loop.py
Called to convert a container into a worker pod for the runpod serverless platform.
"""

import os
import sys
import types
from typing import Dict, Any, Optional

import aiohttp

import runpod.serverless.modules.logging as log
from .modules.heartbeat import HeartbeatSender
from .modules.job import get_job, run_job, run_job_generator
from .modules.rp_http import send_result, stream_result
from .modules.worker_state import REF_COUNT_ZERO, set_job_id
from .utils import rp_debugger

_TIMEOUT = aiohttp.ClientTimeout(total=300, connect=2, sock_connect=2)

heartbeat = HeartbeatSender()


def _get_auth_header() -> Dict[str, str]:
    '''
    Returns the authorization header for the worker.
    '''
    return {"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"}


def _is_local_testing() -> bool:
    '''
    Returns True if the environment variable RUNPOD_WEBHOOK_GET_JOB is not set.
    '''
    return os.environ.get("RUNPOD_WEBHOOK_GET_JOB", None) is None


async def start_worker(config: Dict[str, Any]) -> None:
    """
    Starts the worker loop.
    """
    auth_header = _get_auth_header()

    async with aiohttp.ClientSession(headers=auth_header, timeout=_TIMEOUT) as session:

        heartbeat.start_ping()

        while True:
            job: Optional[Dict[str, Any]] = await get_job(session, config)

            if job is None:
                log.debug("No job available, waiting for the next one.")
                continue

            set_job_id(job["id"])
            log.debug(f"{job['id']} | Set Job ID")

            if job.get('input', None) is None:
                error_msg = f"Job {job['id']} has no input parameter. Unable to run."
                log.error(error_msg)
                job_result = {"error": error_msg}
            elif isinstance(config["handler"], types.GeneratorType):
                job_result = run_job_generator(config["handler"], job)

                log.debug("Handler is a generator, streaming results.")
                for job_stream in job_result:
                    await stream_result(session, job_stream, job)
                job_result = None
            else:
                job_result = run_job(config["handler"], job)

            # If refresh_worker is set, pod will be reset after job is complete.
            if config.get("refresh_worker", False):
                log.info(f"refresh_worker | Flag set, stopping pod after job {job['id']}.")
                job_result["stopPod"] = True

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
            set_job_id(None)

            if _is_local_testing():
                if "error" in job_result:
                    log.error(f"Job {job['id']} failed with error: {job_result['error']}")
                    sys.exit(1)
                else:
                    log.info("Local testing complete, exiting.")
                    sys.exit(0)
