'''
runpod | serverless | pod_worker.py
Called to convert a container into a worker pod for the runpod serverless platform.
'''

import os
import aiohttp
import json
import time
import asyncio

import runpod.serverless.modules.logging as log
from .modules.heartbeat import heartbeat_ping
from .modules.worker_state import job_get_url, set_job_id, get_done_url

timeout = aiohttp.ClientTimeout(total=300, connect=2, sock_connect=2)


async def start_worker(config):
    auth_header = {
        "Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"}
    async with aiohttp.ClientSession(headers=auth_header) as session:

        asyncio.create_task(heartbeat_ping(session))

        while True:

            # GET JOB
            try:
                async with session.get(job_get_url) as response:
                    next_job = await response.json()
                log.info(next_job)
            except Exception as e:
                log.error(
                    f"Error while getting job: {err}")

            if next_job is not None:

                job = next_job
                set_job_id(job["id"])

                if 'input' not in next_job:
                    log.error("No input provided. Erroring out request.")
                    run_return = {
                        "error": "No input provided."
                    }
                    continue

                # DO WORK
                log.info(
                    f"Started working on {job['id']} at {time.time()} UTC")

                run_return = {
                    "error": "Failed to return job output or capture error."
                }

                try:
                    job_output = config["handler"](job)

                    if "error" in job_output:
                        run_return = {
                            "error": job_output['error']
                        }
                    else:
                        run_return = {
                            "output": job_output
                        }

                except Exception as err:    # pylint: disable=broad-except
                    log.error(
                        f"Error while running job {job['id']}: {err}")

                    run_return = {
                        "error": str(err)
                    }

                finally:
                    log.info(
                        f"Finished working on {job['id']} at {time.time()} UTC")
                    log.info(f"Run Returning: {run_return}")

                # SEND RESULTS
                try:

                    job_data = json.dumps(run_return, ensure_ascii=False)

                    headers = {
                        "charset": "utf-8",
                        "Content-Type": "application/x-www-form-urlencoded"
                    }
                    async with session.post(get_done_url(), data=job_data, headers=headers) as resp:
                        print(await resp.text())
                except Exception as err:
                    log.error(
                        f"Error while returning job result {job['id']}: {err}")
                finally:
                    # -------------------------------- Job Cleanup ------------------------------- #
                    set_job_id(None)

            if os.environ.get('RUNPOD_WEBHOOK_GET_JOB', None) is None:
                log.info("Local testing complete, exiting.")
                return
