'''
runpod | serverless | worker_loop.py
Called to convert a container into a worker pod for the runpod serverless platform.
'''

import os
import json
from threading import Thread

import aiohttp

import runpod.serverless.modules.logging as log
from .modules.heartbeat import start_heartbeat
from .modules.job import get_job, run_job, send_result
from .modules.worker_state import set_job_id

timeout = aiohttp.ClientTimeout(total=300, connect=2, sock_connect=2)


async def start_worker(config):
    '''
    starts the worker loop
    '''
    auth_header = {
        "Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"
    }

    async with aiohttp.ClientSession(headers=auth_header) as session:

        heartbeat_thread = Thread(target=start_heartbeat)
        heartbeat_thread.daemon = True
        heartbeat_thread.start()

        while True:
            # GET JOB
            job = await get_job(session)

            if job is None:
                log.info("No job available before idle timeout.")
                continue

            if job["input"] is None:
                log.error("No input parameter provided. Erroring out request.")
                continue

            set_job_id(job["id"])

            job_result = run_job(config["handler"], job)

            job_data = None
            try:
                job_data = json.dumps(job_result, ensure_ascii=False)
            except Exception as err:  # pylint: disable=broad-except
                log.error(
                    f"Error while serializing job result {job['id']}: {err}")
                job_data = json.dumps({"error": "unable to serialize job output"})

            # SEND RESULTS
            await send_result(session, job_data, job)

            set_job_id(None)

            if os.environ.get('RUNPOD_WEBHOOK_GET_JOB', None) is None:
                log.info("Local testing complete, exiting.")
                break
