"""
runpod | serverless | worker_loop.py
Called to convert a container into a worker pod for the runpod serverless platform.
"""

import os
import sys

import aiohttp

import runpod.serverless.modules.logging as log
from .modules.heartbeat import start_ping
from .modules.job import get_job, run_job, send_result
from .modules.worker_state import set_job_id

_TIMEOUT = aiohttp.ClientTimeout(total=300, connect=2, sock_connect=2)


def _get_auth_header() -> dict:
    return {"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"}


def _is_local_testing() -> bool:
    return os.environ.get("RUNPOD_WEBHOOK_GET_JOB", None) is None


async def start_worker(config):
    """
    Starts the worker loop.
    """
    auth_header = _get_auth_header()

    async with aiohttp.ClientSession(headers=auth_header, timeout=_TIMEOUT) as session:

        start_ping()

        while True:
            job = await get_job(session, config)

            if job is None:
                log.info("No job available, waiting for the next one.")
                continue

            if job["input"] is None:
                log.error(f"Job {job['id']} has no input parameter provided. Skipping this job.")
                continue

            set_job_id(job["id"])

            log.info(f"Processing job {job['id']}")
            job_result = run_job(config["handler"], job)

            # If refresh_worker is set, pod will be reset after job is complete.
            if config.get("refresh_worker", False):
                log.info(f"Refresh worker flag set, stopping pod after job {job['id']}.")
                job_result["stopPod"] = True

            await send_result(session, job_result, job)

            set_job_id(None)

            if _is_local_testing():
                if "error" in job_result:
                    log.error(f"Job {job['id']} failed with error: {job_result['error']}")
                    sys.exit(1)
                else:
                    log.info("Local testing complete, exiting.")
                    sys.exit(0)
