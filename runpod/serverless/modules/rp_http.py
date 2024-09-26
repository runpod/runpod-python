"""
    This module is used to handle HTTP requests.
"""

import json
import os

from aiohttp import ClientError
from aiohttp_retry import FibonacciRetry, RetryClient

from runpod.http_client import ClientSession
from runpod.serverless.modules.rp_logger import RunPodLogger

from .worker_state import WORKER_ID

JOB_DONE_URL_TEMPLATE = str(
    os.environ.get("RUNPOD_WEBHOOK_POST_OUTPUT", "JOB_DONE_URL")
)
JOB_DONE_URL = JOB_DONE_URL_TEMPLATE.replace("$RUNPOD_POD_ID", WORKER_ID)

JOB_STREAM_URL_TEMPLATE = str(
    os.environ.get("RUNPOD_WEBHOOK_POST_STREAM", "JOB_STREAM_URL")
)
JOB_STREAM_URL = JOB_STREAM_URL_TEMPLATE.replace("$RUNPOD_POD_ID", WORKER_ID)

log = RunPodLogger()


async def _transmit(client_session: ClientSession, url, job_data):
    """
    Wrapper for transmitting results via POST.
    """
    retry_options = FibonacciRetry(attempts=3)
    retry_client = RetryClient(
        client_session=client_session, retry_options=retry_options
    )

    kwargs = {
        "data": job_data,
        "headers": {
            "charset": "utf-8",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        "raise_for_status": True,
    }

    async with retry_client.post(url, **kwargs) as client_response:
        await client_response.text()


async def _handle_result(
    session: ClientSession, job_data, job, url_template, log_message, is_stream=False
):
    """
    A helper function to handle the result, either for sending or streaming.
    """
    try:
        session.headers["X-Request-ID"] = job["id"]

        serialized_job_data = json.dumps(job_data, ensure_ascii=False)

        is_stream = "true" if is_stream else "false"
        url = url_template.replace("$ID", job["id"]) + f"&isStream={is_stream}"

        await _transmit(session, url, serialized_job_data)
        log.debug(f"{log_message}", job["id"])

    except ClientError as err:
        log.error(f"Failed to return job results. | {err}", job["id"])

    except (TypeError, RuntimeError) as err:
        log.error(f"Error while returning job result. | {err}", job["id"])

    finally:
        # job_data status is used for local development with FastAPI
        if (
            url_template == JOB_DONE_URL
            and job_data.get("status", None) != "IN_PROGRESS"
        ):
            log.info("Finished.", job["id"])


async def send_result(session, job_data, job, is_stream=False):
    """
    Return the job results.
    """
    await _handle_result(
        session, job_data, job, JOB_DONE_URL, "Results sent.", is_stream=is_stream
    )


async def stream_result(session, job_data, job):
    """
    Return the stream job results.
    """
    await _handle_result(
        session, job_data, job, JOB_STREAM_URL, "Intermediate results sent."
    )
