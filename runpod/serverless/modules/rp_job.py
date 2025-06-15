"""
Job related helpers.
"""

import inspect
import json
import os
import traceback
from typing import Any, AsyncGenerator, Callable, Dict, Optional, Union, List

import aiohttp

from runpod.http_client import ClientSession, TooManyRequests
from runpod.serverless.modules.rp_logger import RunPodLogger

from ...version import __version__ as runpod_version
from ..utils import rp_debugger
from .rp_handler import is_generator
from .rp_http import send_result, stream_result
from .rp_tips import check_return_size
from .worker_state import WORKER_ID, REF_COUNT_ZERO, JobsProgress

JOB_GET_URL = str(os.environ.get("RUNPOD_WEBHOOK_GET_JOB")).replace("$ID", WORKER_ID)

log = RunPodLogger()
job_progress = JobsProgress()


def _job_get_url(batch_size: int = 1):
    """
    Prepare the URL for making a 'get' request to the serverless API (sls).

    This function constructs the appropriate URL for sending a 'get' request to the serverless API,
    ensuring that the request will be correctly routed and processed by the API.

    Returns:
        str: The prepared URL for the 'get' request to the serverless API.
    """

    if batch_size > 1:
        job_take_url = JOB_GET_URL.replace("/job-take/", "/job-take-batch/")
        job_take_url += f"&batch_size={batch_size}"
    else:
        job_take_url = JOB_GET_URL

    job_in_progress = "1" if job_progress.get_job_list() else "0"
    job_take_url += f"&job_in_progress={job_in_progress}"

    log.debug(f"rp_job | get_job: {job_take_url}")
    return job_take_url


async def get_job(
    session: ClientSession, num_jobs: int = 1
) -> Optional[List[Dict[str, Any]]]:
    """
    Get a job from the job-take API.

    `num_jobs = 1` will query the legacy singular job-take API.

    `num_jobs > 1` will query the batch job-take API.

    Args:
        session (ClientSession): The aiohttp ClientSession to use for the request.
        num_jobs (int): The number of jobs to get.
    """
    async with session.get(_job_get_url(num_jobs)) as response:
        log.debug(f"rp_job | Response: {type(response).__name__} {response.status}")

        if response.status == 204:
            log.debug("rp_job | Received 204 status, no jobs.")
            return

        if response.status == 400:
            log.debug("rp_job | Received 400 status, expected when FlashBoot is enabled.")
            return

        if response.status == 429:
            raise TooManyRequests(
                response.request_info,
                response.history,
                status=response.status,
                message=response.reason
            )

        # All other errors should raise an exception
        response.raise_for_status()

        # Verify if the content type is JSON
        if response.content_type != "application/json":
            log.debug(f"rp_job | Unexpected content type: {response.content_type}")
            return

        # Check if there is a non-empty content to parse
        if response.content_length == 0:
            log.debug("rp_job | No content to parse.")
            return

        try:
            jobs = await response.json()
            log.debug("rp_job | Received Job(s)")
        except aiohttp.ContentTypeError:
            log.debug(f"rp_job | Response content is not valid JSON. {response.content}")
            return
        except ValueError as json_error:
            log.debug(f"rp_job | Failed to parse JSON response: {json_error}")
            return

        # legacy job-take API
        if isinstance(jobs, dict):
            if "id" not in jobs or "input" not in jobs:
                raise Exception("Job has missing field(s): id or input.")
            return [jobs]

        # batch job-take API
        if isinstance(jobs, list):
            return jobs


async def handle_job(session: ClientSession, config: Dict[str, Any], job) -> dict:
    if is_generator(config["handler"]):
        is_stream = True
        generator_output = run_job_generator(config["handler"], job)
        log.debug("Handler is a generator, streaming results.", job["id"])

        job_result = {"output": []}
        async for stream_output in generator_output:
            log.debug(f"Stream output: {stream_output}", job["id"])

            if type(stream_output.get("output")) == dict:
                if stream_output["output"].get("error"):
                    stream_output = {"error": str(stream_output["output"]["error"])}

            if stream_output.get("error"):
                job_result = stream_output
                break

            if config.get("return_aggregate_stream", False):
                job_result["output"].append(stream_output["output"])

            await stream_result(session, stream_output, job)
    else:
        is_stream = False
        job_result = await run_job(config["handler"], job)

    # If refresh_worker is set, pod will be reset after job is complete.
    if config.get("refresh_worker", False):
        log.info("refresh_worker flag set, stopping pod after job.", job["id"])
        job_result["stopPod"] = True

    # If rp_debugger is set, debugger output will be returned.
    if config.get("rp_args", {}).get("rp_debugger", False) and isinstance(job_result, dict):
        job_result["output"]["rp_debugger"] = rp_debugger.get_debugger_output()
        log.debug("rp_debugger | Flag set, returning debugger output.", job["id"])

        # Calculate ready delay for the debugger output.
        ready_delay = (config["reference_counter_start"] - REF_COUNT_ZERO) * 1000
        job_result["output"]["rp_debugger"]["ready_delay_ms"] = ready_delay
    else:
        log.debug("rp_debugger | Flag not set, skipping debugger output.", job["id"])
        rp_debugger.clear_debugger_output()

    # Send the job result back to JOB_DONE_URL
    await send_result(session, job_result, job, is_stream=is_stream)


async def run_job(handler: Callable, job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the job using the handler.

    Args:
        handler (Callable): The handler function to use.
        job (Dict[str, Any]): The job to run.

    Returns:
        Dict[str, Any]: The result of running the job.
    """
    log.info("Started.", job["id"])
    run_result = {}

    try:
        handler_return = handler(job)
        job_output = (
            await handler_return
            if inspect.isawaitable(handler_return)
            else handler_return
        )

        log.debug(f"Handler output: {job_output}", job["id"])

        if isinstance(job_output, dict):
            error_msg = job_output.pop("error", None)
            refresh_worker = job_output.pop("refresh_worker", None)
            run_result["output"] = job_output

            if error_msg:
                run_result["error"] = error_msg
            if refresh_worker:
                run_result["stopPod"] = True

        elif isinstance(job_output, bool):
            run_result = {"output": job_output}

        else:
            run_result = {"output": job_output}

        if run_result.get("output") == {}:
            run_result.pop("output")

        check_return_size(run_result)  # Checks the size of the return body.

    except Exception as err:
        error_info = {
            "error_type": str(type(err)),
            "error_message": str(err),
            "error_traceback": traceback.format_exc(),
            "hostname": os.environ.get("RUNPOD_POD_HOSTNAME", "unknown"),
            "worker_id": os.environ.get("RUNPOD_POD_ID", "unknown"),
            "runpod_version": runpod_version,
        }

        log.error("Captured Handler Exception", job["id"])
        log.error(json.dumps(error_info, indent=4))
        run_result = {"error": json.dumps(error_info)}

    finally:
        log.debug(f"run_job return: {run_result}", job["id"])

    return run_result


async def run_job_generator(
    handler: Callable, job: Dict[str, Any]
) -> AsyncGenerator[Dict[str, Union[str, Any]], None]:
    """
    Run generator job used to stream output.
    Yields output partials from the generator.
    """
    is_async_gen = inspect.isasyncgenfunction(handler)
    log.debug(
        "Using Async Generator" if is_async_gen else "Using Standard Generator",
        job["id"],
    )

    try:
        job_output = handler(job)

        if is_async_gen:
            async for output_partial in job_output:
                log.debug(f"Async Generator output: {output_partial}", job["id"])
                yield {"output": output_partial}
        else:
            for output_partial in job_output:
                log.debug(f"Generator output: {output_partial}", job["id"])
                yield {"output": output_partial}

    except Exception as err:
        log.error(err, job["id"])
        yield {"error": f"handler: {str(err)} \ntraceback: {traceback.format_exc()}"}
    finally:
        log.info("Finished running generator.", job["id"])
