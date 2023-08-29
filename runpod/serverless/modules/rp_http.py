"""
    This module is used to handle HTTP requests.
"""

import os
import json
import aiohttp
from aiohttp_retry import RetryClient, ExponentialRetry

from runpod.serverless.modules.rp_logger import RunPodLogger
# from .retry import retry
from .worker_state import Jobs, WORKER_ID

JOB_DONE_URL_TEMPLATE = str(os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT', 'JOB_DONE_URL'))
JOB_DONE_URL = JOB_DONE_URL_TEMPLATE.replace('$RUNPOD_POD_ID', WORKER_ID)

JOB_STREAM_URL_TEMPLATE = str(os.environ.get('RUNPOD_WEBHOOK_POST_STREAM', 'JOB_STREAM_URL'))
JOB_STREAM_URL = JOB_STREAM_URL_TEMPLATE.replace('$RUNPOD_POD_ID', WORKER_ID)

log = RunPodLogger()
job_list = Jobs()


# @retry(max_attempts=3, base_delay=1, max_delay=3)
async def _transmit(session, url, job_id, job_data ):
    """
    Wrapper for transmitting results via POST.
    """
    try:
        retry_options = ExponentialRetry(attempts=3)
        retry_client = RetryClient(client_session=session, retry_options=retry_options)

        kwargs = {
            "data": job_data,
            "headers": {"charset": "utf-8", "Content-Type": "application/x-www-form-urlencoded"},
            "raise_for_status": True
            }

        async with retry_client.post(url, **kwargs) as client_response:
            await client_response.text()

    except aiohttp.ClientResponseError as err:
        log.error(f"{job_id} | Client response error while transmitting job. | {err}")


async def _handle_result(session, job_data, job, url_template, log_message):
    """
    A helper function to handle the result, either for sending or streaming.
    """
    try:
        serialized_job_data = json.dumps(job_data, ensure_ascii=False)
        url = url_template.replace('$ID', job['id'])

        await _transmit(session, url, job['id'], serialized_job_data)
        log.debug(f"{job['id']} | {log_message}")

    except (TypeError, RuntimeError) as err:
        log.error(f"Error while returning job result {job['id']}: {err}")

    if url_template == JOB_DONE_URL:
        job_list.remove_job(job["id"])
        log.info(f'{job["id"]} | Finished')


async def send_result(session, job_data, job):
    """
    Return the job results.
    """
    await _handle_result(session, job_data, job, JOB_DONE_URL, "Results sent.")


async def stream_result(session, job_data, job):
    """
    Return the stream job results.
    """
    await _handle_result(session, job_data, job, JOB_STREAM_URL, "Intermediate results sent.")
