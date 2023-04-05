"""
runpod | serverless | worker_loop.py
Called to convert a container into a worker pod for the runpod serverless platform.
"""

import os

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
            job = await get_job(session)

            if job is None:
                log.info("No job available before idle timeout.")
                continue

            if job["input"] is None:
                log.error("No input parameter provided. Erroring out request.")
                continue

            set_job_id(job["id"])

            job_result = run_job(config["handler"], job)

            await send_result(session, job_result, job)

            set_job_id(None)

            if _is_local_testing():
                log.info("Local testing complete, exiting.")
                break
