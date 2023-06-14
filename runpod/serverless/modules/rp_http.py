"""
    This module is used to handle HTTP requests.
"""

import json

import runpod.serverless.modules.logging as log
from .retry import retry
from .worker_state import IS_LOCAL_TEST, get_done_url, get_stream_url


@retry(max_attempts=3, base_delay=1, max_delay=3)
async def transmit(session, job_data, url):
    """
    Wrapper for sending results.
    """
    headers = {
        "charset": "utf-8",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    log.debug(f"Initiating result API call to {url} with job data: {job_data}")
    async with session.post(url,
                            data=job_data,
                            headers=headers,
                            raise_for_status=True) as resp:
        result = await resp.text()

    log.debug(f"Completed result API call to {url}. Response: {result}")


async def send_result(session, job_data, job):
    '''
    Return the job results.
    '''
    try:
        job_data = json.dumps(job_data, ensure_ascii=False)
        if not IS_LOCAL_TEST:
            await transmit(session, job_data, get_done_url())
            log.debug(f"{job['id']} | Results sent.")
        else:
            log.warn(f"Local test job results for {job['id']}: {job_data}")

    except Exception as err:  # pylint: disable=broad-except
        log.error(f"Error while returning job result {job['id']}: {err}")


async def stream_result(session, job_data, job):
    '''
    Return the stream job results.
    '''
    try:
        job_data = json.dumps(job_data, ensure_ascii=False)
        if not IS_LOCAL_TEST:
            await transmit(session, job_data, get_stream_url())
        else:
            log.warn(f"Local test job results for {job['id']}: {job_data}")

    except Exception as err:  # pylint: disable=broad-except
        log.error(f"Error while returning job result {job['id']}: {err}")
