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
    Run the job using the handler.
    Returns the job output or error.
    '''
    log.info(f"Started working on {job['id']} at {time.time()} UTC")

    run_result = {"error": "Failed to return job output or capture error."}

    try:
        job_output = handler(job)

        if isinstance(job_output, bool):
            run_result = {"output": job_output}
        elif "error" in job_output:
            run_result = {"error": str(job_output['error'])}
        else:
            run_result = {"output": job_output}

    except Exception as err:    # pylint: disable=broad-except
        log.error(f"Error while running job {job['id']}: {err}")

        run_result = {"error": str(err)}

    finally:
        log.info(f"Finished working on {job['id']} at {time.time()} UTC")
        log.info(f"Run result: {run_result}")

        return run_result  # pylint: disable=lost-exception


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
        job_data = json.dumps(job_data, ensure_ascii=False)

        if os.environ.get('RUNPOD_WEBHOOK_GET_JOB', None) is not None:
            log.info(f"Sending job results: {job_data}")
            await retry_send_result(session, job_data)
        else:
            log.warn(f"Local test job results: {job_data}")

    except Exception as err:  # pylint: disable=broad-except
        log.error(f"Error while returning job result {job['id']}: {err}")
    else:
        log.info(f"Successfully returned job result {job['id']}")


# ------------------------------- Local Testing ------------------------------ #
def get_local():
    '''
    Returns contents of test_input.json
    '''
    if not os.path.exists('test_input.json'):
        log.warn('test_input.json not found, skipping local testing')
        return None

    with open('test_input.json', 'r', encoding="UTF-8") as file:
        test_inputs = json.loads(file.read())

    if 'id' not in test_inputs:
        test_inputs['id'] = 'local_test'

    return test_inputs
