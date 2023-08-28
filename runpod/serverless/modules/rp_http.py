"""
    This module is used to handle HTTP requests.
"""

import os
import json

from runpod.serverless.modules.rp_logger import RunPodLogger
from .retry import retry
from .worker_state import Jobs, WORKER_ID

# Constants
JOB_DONE_URL_TEMPLATE = str(os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT', 'JOB_DONE_URL_TEMPLATE'))
JOB_DONE_URL_TEMPLATE = JOB_DONE_URL_TEMPLATE.replace('$RUNPOD_POD_ID', WORKER_ID)

JOB_STREAM_URL_TEMPLATE = str(os.environ.get('RUNPOD_WEBHOOK_POST_STREAM', 'JOB_STREAM_URL_TEMPLATE'))
JOB_STREAM_URL_TEMPLATE = JOB_STREAM_URL_TEMPLATE.replace('$RUNPOD_POD_ID', WORKER_ID)

HEADERS = {
    "charset": "utf-8",
    "Content-Type": "application/x-www-form-urlencoded"
}

log = RunPodLogger()
job_list = Jobs()


@retry(max_attempts=3, base_delay=1, max_delay=3)
async def transmit(session, job_data, url):
    """
    Wrapper for sending results.
    """
    async with session.post(url,
                            data=job_data,
                            headers=HEADERS,
                            raise_for_status=True) as resp:
        await resp.text()


async def _handle_result(session, job_data, job, url_template, log_message):
    """
    A helper function to handle the result, either for sending or streaming.
    """
    try:
        serialized_job_data = json.dumps(job_data, ensure_ascii=False)
        url = url_template.replace('$ID', job['id'])

        await transmit(session, serialized_job_data, url)
        log.debug(f"{job['id']} | {log_message}")

    except Exception as err: # pylint: disable=broad-except
        log.error(f"Error while returning job result {job['id']}: {err}")

    if url_template == JOB_DONE_URL_TEMPLATE:
        job_list.remove_job(job["id"])
        log.info(f'{job["id"]} | Finished')


async def send_result(session, job_data, job):
    """
    Return the job results.
    """
    await _handle_result(session, job_data, job, JOB_DONE_URL_TEMPLATE, "Results sent.")


async def stream_result(session, job_data, job):
    """
    Return the stream job results.
    """
    await _handle_result(
        session, job_data, job, JOB_STREAM_URL_TEMPLATE, "Intermediate Results sent.")
