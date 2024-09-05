"""
Job related helpers.
"""
# pylint: disable=too-many-branches

import inspect
from typing import Any, Callable, Dict, List, Optional, Union, AsyncGenerator

import os
import json
import asyncio
import traceback

from runpod.http_client import ClientSession
from runpod.serverless.modules.rp_logger import RunPodLogger
from .worker_state import WORKER_ID, JobsQueue
from .rp_tips import check_return_size
from ...version import __version__ as runpod_version

JOB_GET_URL = str(os.environ.get('RUNPOD_WEBHOOK_GET_JOB')).replace('$ID', WORKER_ID)

log = RunPodLogger()
job_list = JobsQueue()


def _job_get_url(batch_size: int = 1):
    """
    Prepare the URL for making a 'get' request to the serverless API (sls).

    This function constructs the appropriate URL for sending a 'get' request to the serverless API,
    ensuring that the request will be correctly routed and processed by the API.

    Returns:
        str: The prepared URL for the 'get' request to the serverless API.
    """
    job_in_progress = '1' if job_list.get_job_count() else '0'

    if batch_size > 1:
        job_take_url = JOB_GET_URL.replace("/job-take/", "/job-take-batch/")
        job_take_url += f"&batch_size={batch_size}"
    else:
        job_take_url = JOB_GET_URL

    return job_take_url + f"&job_in_progress={job_in_progress}"


async def get_job(session: ClientSession, jobs_needed=1, retry=True) -> Optional[List[Dict[str, Any]]]:  # pylint: disable=line-too-long, too-many-statements
    """
    Get the job from the queue.
    Will continue trying to get a job until one is available.

    Args:
        session (ClientSession): The aiohttp ClientSession to use for the request.
        jobs_needed (int): The number of jobs to get.
        retry (bool): Whether to retry if no job is available.

    Note: Retry True just for ease of, if testing improved this can be removed.
    """
    while True:
        try:
            async with session.get(_job_get_url(jobs_needed)) as response:
                if response.status == 204:
                    log.debug("No content, no job to process.")
                    if retry is False:
                        break
                    continue

                if response.status == 400:
                    log.debug("Received 400 status, expected when FlashBoot is enabled.")
                    if retry is False:
                        break
                    continue

                if response.status != 200:
                    log.error(f"Failed to get job, status code: {response.status}")
                    if retry is False:
                        break
                    continue

                received_request = await response.json()
                log.debug(f"Request Received | {received_request}")

                if isinstance(received_request, dict):
                    # Check if the job is valid
                    job_id = received_request.get("id", None)
                    job_input = received_request.get("input", None)

                    if None in [job_id, job_input]:
                        missing_fields = []
                        if job_id is None:
                            missing_fields.append("id")
                        if job_input is None:
                            missing_fields.append("input")

                        log.error(f"Job has missing field(s): {', '.join(missing_fields)}.")
                    else:
                        job_list.add_job(received_request["id"])
                        log.debug("Request ID added.", received_request['id'])

                        return [received_request]

                if isinstance(received_request, list):
                    for job in received_request:
                        job_list.add_job(job["id"])
                        log.debug("Request ID added.", job['id'])

                    return received_request

        except asyncio.TimeoutError:
            log.debug("Timeout error, retrying.")
            if retry is False:
                break

        except Exception as err:  # pylint: disable=broad-except
            err_type = type(err).__name__
            err_message = str(err)
            err_traceback = traceback.format_exc()
            log.error(f"Failed to get job. | Error Type: {err_type} | Error Message: {err_message}")
            log.error(f"Traceback: {err_traceback}")

        log.debug("No job available, waiting for the next one.")
        if retry is False:
            break

        await asyncio.sleep(0)


async def run_job(handler: Callable, job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the job using the handler.

    Args:
        handler (Callable): The handler function to use.
        job (Dict[str, Any]): The job to run.

    Returns:
        Dict[str, Any]: The result of running the job.
    """
    log.info('Started.', job["id"])
    run_result = {}

    try:
        handler_return = handler(job)
        job_output = await handler_return if inspect.isawaitable(handler_return) else handler_return

        log.debug(f'Handler output: {job_output}', job["id"])

        if isinstance(job_output, dict):
            error_msg = job_output.pop("error", None)
            refresh_worker = job_output.pop("refresh_worker", None)
            run_result['output'] = job_output

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

    except Exception as err:    # pylint: disable=broad-except
        error_info = {
            "error_type": str(type(err)),
            "error_message": str(err),
            "error_traceback": traceback.format_exc(),
            "hostname": os.environ.get("RUNPOD_POD_HOSTNAME", "unknown"),
            "worker_id": os.environ.get("RUNPOD_POD_ID", "unknown"),
            "runpod_version": runpod_version
        }

        log.error('Captured Handler Exception', job["id"])
        log.error(json.dumps(error_info, indent=4))
        run_result = {"error": json.dumps(error_info)}

    finally:
        log.debug(f'run_job return: {run_result}', job["id"])

    return run_result


async def run_job_generator(
        handler: Callable,
        job: Dict[str, Any]) -> AsyncGenerator[Dict[str, Union[str, Any]], None]:
    '''
    Run generator job used to stream output.
    Yields output partials from the generator.
    '''
    is_async_gen = inspect.isasyncgenfunction(handler)
    log.debug('Using Async Generator' if is_async_gen else 'Using Standard Generator', job["id"])

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

    except Exception as err:    # pylint: disable=broad-except
        log.error(err, job["id"])
        yield {
            "error": f"handler: {str(err)} \ntraceback: {traceback.format_exc()}"
        }
    finally:
        log.info('Finished running generator.', job["id"])
