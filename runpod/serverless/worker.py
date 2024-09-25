"""
runpod | serverless | worker_loop.py
Called to convert a container into a worker pod for the runpod serverless platform.
"""

import asyncio
import os
from typing import Any, Dict

from runpod.http_client import AsyncClientSession
from runpod.serverless.modules import rp_handler, rp_local, rp_logger, rp_ping, rp_scale

from .modules.rp_http import send_result, stream_result
from .modules.rp_job import run_job, run_job_generator
from .modules.worker_state import REF_COUNT_ZERO, Jobs
from .utils import rp_debugger

log = rp_logger.RunPodLogger()
job_list = Jobs()
heartbeat = rp_ping.Heartbeat()


def _is_local(config) -> bool:
    """Returns True if the worker is running locally, False otherwise."""
    if config["rp_args"].get("test_input", None):
        return True

    if os.environ.get("RUNPOD_WEBHOOK_GET_JOB", None) is None:
        return True

    return False


async def _process_job(job, session, job_scaler, config):
    if rp_handler.is_generator(config["handler"]):
        is_stream = True
        generator_output = run_job_generator(config["handler"], job)
        log.debug("Handler is a generator, streaming results.", job["id"])

        job_result = {"output": []}
        async for stream_output in generator_output:
            log.debug(f"Stream output: {stream_output}", job["id"])
            if "error" in stream_output:
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
        job_scaler.kill_worker()

    # If rp_debugger is set, debugger output will be returned.
    if config["rp_args"].get("rp_debugger", False) and isinstance(job_result, dict):
        job_result["output"]["rp_debugger"] = rp_debugger.get_debugger_output()
        log.debug("rp_debugger | Flag set, returning debugger output.", job["id"])

        # Calculate ready delay for the debugger output.
        ready_delay = (config["reference_counter_start"] - REF_COUNT_ZERO) * 1000
        job_result["output"]["rp_debugger"]["ready_delay_ms"] = ready_delay
    else:
        log.debug("rp_debugger | Flag not set, skipping debugger output.", job["id"])
        rp_debugger.clear_debugger_output()

    # Send the job result to SLS
    await send_result(session, job_result, job, is_stream=is_stream)


# ------------------------- Main Worker Running Loop ------------------------- #
async def run_worker(config: Dict[str, Any]) -> None:
    """
    Starts the worker loop for multi-processing.

    This function is called when the worker is running on RunPod. This function
    starts a loop that runs indefinitely until the worker is killed.

    Args:
        config (Dict[str, Any]): Configuration parameters for the worker.
    """
    # Start pinging RunPod to show that the worker is alive.
    heartbeat.start_ping()

    # Create an async session that will be closed when the worker is killed.
    async with AsyncClientSession() as session:
        # Create a JobScaler responsible for adjusting the concurrency
        # of the worker based on the modifier callable.
        job_scaler = rp_scale.JobScaler(
            concurrency_modifier=config.get("concurrency_modifier", None)
        )

        # Create a task that will run the get_jobs method in the background.
        # This task will fetch jobs from RunPod and add them to the queue.
        jobtake_task = asyncio.create_task(job_scaler.get_jobs(session))

        # Create a task that will run the run_jobs method in the background.
        # This task will process jobs from the queue.
        jobrun_task = asyncio.create_task(job_scaler.run_jobs(session, config))

        # Concurrently run both tasks and wait for both to finish.
        await asyncio.gather(jobtake_task, jobrun_task)


def main(config: Dict[str, Any]) -> None:
    """
    Checks if the worker is running locally or on RunPod.
    If running locally, the test job is run and the worker exits.
    If running on RunPod, the worker loop is created.
    """
    if _is_local(config):
        asyncio.run(rp_local.run_local(config))

    else:
        asyncio.run(run_worker(config))
