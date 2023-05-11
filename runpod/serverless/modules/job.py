"""
Job related helpers.
"""

import os
import time
import json
import traceback

import runpod.serverless.modules.logging as log
from .worker_state import JOB_GET_URL, get_done_url
from .retry import retry
from .rp_tips import check_return_size

_IS_LOCAL_TEST = os.environ.get("RUNPOD_WEBHOOK_GET_JOB", None) is None


def _get_local():
    """
    Returns contents of test_input.json.
    """
    if not os.path.exists("test_input.json"):
        log.warn("test_input.json not found, skipping local testing")
        return None

    with open("test_input.json", "r", encoding="UTF-8") as file:
        test_inputs = json.loads(file.read())

    if "id" not in test_inputs:
        test_inputs["id"] = "local_test"

    log.debug(f"Retrieved local job: {test_inputs}")
    return test_inputs


async def get_job(session, config):
    """
    Get the job from the queue.
    """
    next_job = None

    try:
        if config.get("test_input", None) is not None:
            log.warn("test_input set, using test_input as job input")
            next_job = config["test_input"]
            next_job["id"] = "test_input_provided"
        elif _IS_LOCAL_TEST:
            log.warn("RUNPOD_WEBHOOK_GET_JOB not set, switching to get_local")
            next_job = _get_local()
        else:
            async with session.get(JOB_GET_URL) as response:
                next_job = await response.json()
                log.debug(f"Retrieved remote job: {next_job}")

        if next_job is not None:
            log.info(f"Received job: {next_job['id']}")
    except Exception as err:  # pylint: disable=broad-except
        log.error(f"Error while getting job: {err}")

    return next_job


def run_job(handler, job):
    """
    Run the job using the handler.
    Returns the job output or error.
    """
    start_time = time.time()
    log.info(f'Started working on job {job["id"]} at {start_time} UTC')

    run_result = {"error": "Failed to return job output or capture error."}

    try:
        job_output = handler(job)
        log.debug(f'Job {job["id"]} handler output: {job_output}')

        if isinstance(job_output, bool):
            run_result = {"output": job_output}
        elif "error" in job_output:
            run_result = {"error": str(job_output["error"])}
        elif "refresh_worker" in job_output:
            job_output.pop("refresh_worker")
            run_result = {
                "stopPod": True,
                "output": job_output
            }
        else:
            run_result = {"output": job_output}

        check_return_size(run_result)  # Checks the size of the return body.
    except Exception as err:    # pylint: disable=broad-except
        log.error(f'Error while running job {job["id"]}: {err}')
        run_result = {"error": f"handler: {str(err)} \ntraceback: {traceback.format_exc()}"}
    finally:
        end_time = time.time()
        log.info(f'Finished working on job {job["id"]} at {end_time} UTC')
        log.info(f"Job {job['id']} took {end_time - start_time} seconds to complete")
        log.debug(f"Run result: {run_result}")

        return run_result  # pylint: disable=lost-exception


@retry(max_attempts=3, base_delay=1, max_delay=3)
async def retry_send_result(session, job_data):
    """
    Wrapper for sending results.
    """
    headers = {
        "charset": "utf-8",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    log.debug("Initiating result API call")
    async with session.post(get_done_url(),
                            data=job_data,
                            headers=headers,
                            raise_for_status=True) as resp:
        result = await resp.text()
        log.debug(f"Result API response: {result}")

    log.info("Completed result API call")


async def send_result(session, job_data, job):
    '''
    Return the job results.
    '''
    try:
        job_data = json.dumps(job_data, ensure_ascii=False)
        if not _IS_LOCAL_TEST:
            log.info(f"Sending job results for {job['id']}: {job_data}")
            await retry_send_result(session, job_data)
        else:
            log.warn(f"Local test job results for {job['id']}: {job_data}")

    except Exception as err:  # pylint: disable=broad-except
        log.error(f"Error while returning job result {job['id']}: {err}")
    else:
        log.info(f"Successfully returned job result {job['id']}")
