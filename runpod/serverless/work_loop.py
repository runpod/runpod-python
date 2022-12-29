'''
runpod | serverless | pod_worker.py
Called to convert a container into a worker pod for the runpod serverless platform.
'''

import os
import aiohttp
import asyncio


import runpod.serverless.modules.logging as log
from .modules.heartbeat import heartbeat_ping
from .modules.job import get_job, run_job, send_result
from .modules.worker_state import set_job_id

timeout = aiohttp.ClientTimeout(total=300, connect=2, sock_connect=2)


async def start_worker(config):
    auth_header = {
        "Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"}
    async with aiohttp.ClientSession(headers=auth_header) as session:

        asyncio.create_task(heartbeat_ping(session))

        while True:

            # GET JOB
            job = await get_job(session)

            if job is not None:
                set_job_id(job["id"])
            else:
                continue

            job_result = run_job(config["handler"], job) if job else {
                "error": "No input provided."
            }

            # SEND RESULTS
            await send_result(session, job_result, job)

            set_job_id(None)

            if os.environ.get('RUNPOD_WEBHOOK_GET_JOB', None) is None:
                log.info("Local testing complete, exiting.")
                return
