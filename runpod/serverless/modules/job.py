'''
job related helpers
'''

import os
import time
import json

import runpod.serverless.modules.logging as log
from .worker_state import JOB_GET_URL, get_done_url
from .retry import retry


async def get_job(session):
    '''
    Get the job from the queue.
    '''
    next_job = None

    try:
        if os.environ.get('RUNPOD_WEBHOOK_GET_JOB', None) is None:
            log.warn('RUNPOD_WEBHOOK_GET_JOB not set, switching to get_local')
            next_job = get_local()
        else:
            async with session.get(JOB_GET_URL) as response:
                next_job = await response.json()

        log.info(next_job)
    except Exception as err:  # pylint: disable=broad-except
        log.error(f"Error while getting job: {err}")

    return next_job


def run_job(handler, job):
    '''
    run the handler and format the return
    '''
    log.info(f"Started working on {job['id']} at {time.time()} UTC")

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
        log.error(f"Error while running job {job['id']}: {err}")

        return {
            "error": str(err)
        }

    finally:
        log.info(f"Finished working on {job['id']} at {time.time()} UTC")


@retry(max_attempts=3, base_delay=1, max_delay=3)
async def retry_send_result(session, job_data):
    '''
    wrapper for sending results
    '''
    headers = {
        "charset": "utf-8",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    log.info("result api call")
    async with session.post(get_done_url(),
                            data=job_data,
                            headers=headers,
                            raise_for_status=True) as resp:
        result = await resp.text()
        log.debug(result)

    log.info("done with result api call")


async def send_result(session, job_data, job):
    '''
    Return the job results.
    '''
    try:
        log.info("sending results")
        await retry_send_result(session, job_data)
    except Exception as err:  # pylint: disable=broad-except
        log.error(f"Error while returning job result {job['id']}: {err}")


# ------------------------------- Local Testing ------------------------------ #
def get_local():
    '''
    Returns contents of test_inputs.json
    '''
    if not os.path.exists('test_inputs.json'):
        log.warn('test_inputs.json not found, skipping local testing')
        return None

    with open('test_inputs.json', 'r', encoding="UTF-8") as file:
        test_inputs = json.loads(file.read())

    if 'id' not in test_inputs:
        test_inputs['id'] = 'local_test'

    return test_inputs
