'''
job related helpers
'''
import time

import runpod.serverless.modules.logging as log
from .worker_state import JOB_GET_URL, get_done_url
from .retry import retry


async def get_job(session):
    '''
    get the job from the queue
    '''
    next_job = None

    try:
        async with session.get(JOB_GET_URL) as response:
            next_job = await response.json()
        log.info(next_job)
    except Exception as err:  # pylint: disable=broad-except
        log.error(
            f"Error while getting job: {err}")

    return next_job


def run_job(handler, job):
    '''
    run the handler and format the return
    '''
    log.info(
        f"Started working on {job['id']} at {time.time()} UTC")

    try:
        job_output = handler(job)

        if "error" in job_output:
            return {
                "error": job_output['error']
            }
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


@retry(max_attempts=3, base_delay=1, max_delay=3)
async def retry_send_result(session, job_data):
    '''
    wrapper for sending results
    '''
    headers = {
        "charset": "utf-8",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    async with session.post(get_done_url(),
                            data=job_data,
                            headers=headers,
                            raise_for_status=True) as resp:
        await resp.text()


async def send_result(session, job_data, job):
    '''
    try except wrapper
    '''
    try:
        await retry_send_result(session, job_data)
    except Exception as err:  # pylint: disable=broad-except
        log.error(
            f"Error while returning job result {job['id']}: {err}")
