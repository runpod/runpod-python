"""
Job related helpers.
"""
# pylint: disable=too-many-branches

import inspect
from typing import Any, Callable, Dict, Optional, Union, AsyncGenerator

import os
import json
import asyncio
import traceback

from runpod.http_client import ClientSession
from runpod.serverless.modules.rp_logger import RunPodLogger
from .worker_state import WORKER_ID, Jobs
from .rp_tips import check_return_size
from ...version import __version__ as runpod_version

JOB_GET_URL = str(os.environ.get('RUNPOD_WEBHOOK_GET_JOB')).replace('$ID', WORKER_ID)

log = RunPodLogger()
job_list = Jobs()


def _job_get_url():
    """
    Prepare the URL for making a 'get' request to the serverless API (sls).

    This function constructs the appropriate URL for sending a 'get' request to the serverless API,
    ensuring that the request will be correctly routed and processed by the API.

    Returns:
        str: The prepared URL for the 'get' request to the serverless API.
    """
    job_in_progress = '1' if job_list.get_job_list() else '0'
    return JOB_GET_URL + f"&job_in_progress={job_in_progress}"


async def get_job(session: ClientSession, retry: bool = True) -> Optional[Dict[str, Any]]:
    """
    Get the job from the queue.
    Will continue trying to get a job until one is available.

    Args:
        session (ClientSession): The async http client to use for the request.
        retry (bool): Whether to retry if no job is available.
    """
    while True:
        if retry:
            await asyncio.sleep(1)

        try:
            async with session.get(_job_get_url()) as response:
                if response.status == 204:
                    log.debug("No content, no job to process.")
                    if not retry:
                        break
                    continue

                if response.status == 400:
                    log.debug("Received 400 status, expected when FlashBoot is enabled.")
                    if not retry:
                        break
                    continue

                if response.status != 200:
                    log.error(f"Failed to get job, status code: {response.status}")
                    if not retry:
                        break
                    continue

                job = await response.json()
                log.debug(f"Request Received | {job}")
                if not isinstance(job, dict) or "id" not in job or "input" not in job:
                    log.error("Job has missing fields: id or input.")
                    if not retry:
                        break
                    continue

                job_list.add_job(job["id"])
                return job

        except asyncio.TimeoutError:
            pass

        except Exception as error:  # pylint: disable=broad-except
            log.error(f"Failed to get job: {type(error).__name__} - {str(error)}")

        if not retry:
            break

    return None


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
