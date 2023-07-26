"""
    This module is used to handle HTTP requests.
"""

import os
import json

from runpod.serverless.modules.rp_logger import RunPodLogger
from .worker_state import Jobs
from .retry import retry
from .worker_state import WORKER_ID

JOB_DONE_URL_TEMPLATE = str(os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT'))
JOB_DONE_URL_TEMPLATE = JOB_DONE_URL_TEMPLATE.replace('$RUNPOD_POD_ID', WORKER_ID)

JOB_STREAM_URL_TEMPLATE = str(os.environ.get('RUNPOD_WEBHOOK_POST_STREAM'))
JOB_STREAM_URL_TEMPLATE = JOB_STREAM_URL_TEMPLATE.replace('$RUNPOD_POD_ID', WORKER_ID)

log = RunPodLogger()
job_list = Jobs()


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
        job_done_url = JOB_DONE_URL_TEMPLATE.replace('$ID', job['id'])

        await transmit(session, job_data, job_done_url)
        log.debug(f"{job['id']} | Results sent.")

        job_list.remove_job(job["id"])
        log.info(f'{job["id"]} | Finished')

    except Exception as err:  # pylint: disable=broad-except
        log.error(f"Error while returning job result {job['id']}: {err}")

async def stream_result(session, job_data, job):
    '''
    Return the stream job results.
    '''
    try:
        job_data = json.dumps(job_data, ensure_ascii=False)
        job_done_url = JOB_STREAM_URL_TEMPLATE.replace('$ID', job['id'])

        await transmit(session, job_data, job_done_url)

    except Exception as err:  # pylint: disable=broad-except
        log.error(f"Error while returning job result {job['id']}: {err}")
