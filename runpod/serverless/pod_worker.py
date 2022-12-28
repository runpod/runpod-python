'''
runpod | serverless | pod_worker.py
Called to convert a container into a worker pod for the runpod serverless platform.
'''

import os
import aiohttp
import json
import time
import uuid
import asyncio

from .modules.logging import log


async def start_worker(config):

    worker_id = os.environ.get('RUNPOD_POD_ID', str(uuid.uuid4()))

    auth_header = {"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"}
    get_work_url = str(os.environ.get('RUNPOD_WEBHOOK_GET_JOB')).replace(
        '$ID', worker_id)
    job_done_url = str(os.environ.get(
        'RUNPOD_WEBHOOK_POST_OUTPUT'))
    job_done_url = job_done_url.replace(
        '$RUNPOD_POD_ID', worker_id)

    async with aiohttp.ClientSession(headers=auth_header) as session:

        async def heartbeat_ping():
            webhook_ping = os.environ.get('RUNPOD_WEBHOOK_PING', None)
            ping_interval = int(os.environ.get('RUNPOD_PING_INTERVAL', 10000))
            headers = {
                "Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"}

            if webhook_ping is not None:
                webhook_ping = webhook_ping.replace(
                    '$RUNPOD_POD_ID', worker_id)
                while True:
                    asyncio.create_task(
                        session.get(webhook_ping, headers=headers,
                                    timeout=int(ping_interval/1000))
                    )

                    await asyncio.sleep(ping_interval/1000)

        asyncio.create_task(heartbeat_ping())

        while True:

            # GET JOB
            try:
                start_time = time.time()
                async with session.get(get_work_url) as response:
                    next_job = await response.json()
                log(f"got job {time.time() - start_time}", "INFO")
                log(next_job)
            except Exception as e:
                log("Uncaught exception while getting job")

            if next_job is not None:

                job = next_job

                if 'input' not in next_job:
                    log("No input provided. Erroring out request.", "ERROR")
                    run_return = {
                        "error": "No input provided."
                    }
                    continue

                # DO WORK
                try:
                    log(f"Started working on {job['id']} at {time.time()} UTC", "INFO")

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
                        log(
                            f"Error while running job {job['id']}: {err}", "ERROR")

                        run_return = {
                            "error": str(err)
                        }

                    finally:
                        log(
                            f"Finished working on {job['id']} at {time.time()} UTC", "INFO")
                        log(f"Run Returning: {run_return}", "INFO")

                except Exception as err:
                    run_return = {
                        "error": str(err)
                    }
                finally:
                    # -------------------------------- Job Cleanup ------------------------------- #
                    job_id = None

                # SEND RESULTS
                try:

                    job_data = json.dumps(run_return, ensure_ascii=False)

                    if os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT', None) is None:
                        log("RUNPOD_WEBHOOK_POST_OUTPUT not set, skipping completing job", 'WARNING')
                        return
                    job_done_url = job_done_url.replace('$ID', next_job['id'])
                    headers = {
                        "charset": "utf-8",
                        "Content-Type": "application/x-www-form-urlencoded"
                    }

                    async with session.post(job_done_url, data=job_data, headers=headers) as resp:
                        print(await resp.text())
                except Exception as err:
                    log(
                        f"Error while returning job result {job['id']}: {err}", "ERROR")
                finally:
                    # -------------------------------- Job Cleanup ------------------------------- #
                    job_id = None

            if os.environ.get('RUNPOD_WEBHOOK_GET_JOB', None) is None:
                log("Local testing complete, exiting.")
                return
