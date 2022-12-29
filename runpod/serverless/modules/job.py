import time
import json
import asyncio
import random

import runpod.serverless.modules.logging as log
from .worker_state import job_get_url, get_done_url


async def get_job(session):
    next_job = None

    try:
        async with session.get(job_get_url) as response:
            next_job = await response.json()
        log.info(next_job)
    except Exception as err:
        log.error(
            f"Error while getting job: {err}")

    return next_job


def run_job(handler, job):
    # DO WORK
    log.info(
        f"Started working on {job['id']} at {time.time()} UTC")

    try:
        job_output = handler(job)

        if "error" in job_output:
            return {
                "error": job_output['error']
            }
        else:
            return {
                "output": job_output
            }

    except Exception as err:    # pylint: disable=broad-except
        log.error(
            f"Error while running job {job['id']}: {err}")

        return {
            "error": str(err)
        }

    finally:
        log.info(
            f"Finished working on {job['id']} at {time.time()} UTC")


async def send_result(session, result, job):
    try:
        job_data = json.dumps(result, ensure_ascii=False)
    except Exception as err:
        log.error(
            f"Error while serializing job result {job['id']}: {err}")
        return

    attempts = 1
    success = False

    while attempts <= 3 and success == False:
        try:
            headers = {
                "charset": "utf-8",
                "Content-Type": "application/x-www-form-urlencoded"
            }

            async with session.post(get_done_url(), data=job_data, headers=headers, raise_for_status=True) as resp:
                print(await resp.text())
            success = True
        except Exception as err:
            attempts += 1
            await asyncio.sleep(random.randint(1, 3))
            log.error(
                f"Error while returning job result {job['id']}: {err}")
