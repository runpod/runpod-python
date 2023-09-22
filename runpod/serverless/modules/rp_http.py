"""
    This module is used to handle HTTP requests.
"""

import os
import json
import aiohttp
from aiohttp_retry import RetryClient, ExponentialRetry

from runpod.serverless.modules.rp_logger import RunPodLogger
from .worker_state import Jobs, WORKER_ID

JOB_DONE_URL_TEMPLATE = str(os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT', 'JOB_DONE_URL'))
JOB_DONE_URL = JOB_DONE_URL_TEMPLATE.replace('$RUNPOD_POD_ID', WORKER_ID)

JOB_STREAM_URL_TEMPLATE = str(os.environ.get('RUNPOD_WEBHOOK_POST_STREAM', 'JOB_STREAM_URL'))
JOB_STREAM_URL = JOB_STREAM_URL_TEMPLATE.replace('$RUNPOD_POD_ID', WORKER_ID)

log = RunPodLogger()
job_list = Jobs()


async def _transmit(client_session, url, job_data ):
    """
    Wrapper for transmitting results via POST.
    """
    retry_options = ExponentialRetry(attempts=3)
    retry_client = RetryClient(client_session=client_session, retry_options=retry_options)

    kwargs = {
        "data": job_data,
        "headers": {"charset": "utf-8", "Content-Type": "application/x-www-form-urlencoded"},
        "raise_for_status": True
        }

    async with retry_client.post(url, **kwargs) as client_response:
        await client_response.text()


async def _handle_result(session, job_data, job, url_template, log_message):
    """
    A helper function to handle the result, either for sending or streaming.
    """
    try:
        serialized_job_data = json.dumps(job_data, ensure_ascii=False)
        url = url_template.replace('$ID', job['id'])

        await _transmit(session, url, serialized_job_data)
        log.debug(f"{job['id']} | {log_message}")

    except aiohttp.ClientError as err:
        log.error(f"{job['id']} | Failed to return job results. | {err}")

    except (TypeError, RuntimeError) as err:
        log.error(f"Error while returning job result {job['id']}: {err}")

    finally:
        if url_template == JOB_DONE_URL and job_data.get('status', None) != 'IN_PROGRESS':
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
