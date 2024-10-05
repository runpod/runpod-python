"""
runpod | serverless | worker_loop.py
Called to convert a container into a worker pod for the runpod serverless platform.
"""

import asyncio
import os
from typing import Any, Dict

from runpod.http_client import AsyncClientSession
from runpod.serverless.modules import rp_logger, rp_local, rp_ping, rp_scale

log = rp_logger.RunPodLogger()
heartbeat = rp_ping.Heartbeat()


def _is_local(config) -> bool:
    """Returns True if the worker is running locally, False otherwise."""
    if config["rp_args"].get("test_input", None):
        return True

    if os.environ.get("RUNPOD_WEBHOOK_GET_JOB", None) is None:
        return True

    return False


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

        # Create tasks for getting and running jobs.
        jobtake_task = asyncio.create_task(job_scaler.get_jobs(session))
        jobrun_task = asyncio.create_task(job_scaler.run_jobs(session, config))

        tasks = [jobtake_task, jobrun_task]

        try:
            # Concurrently run both tasks and wait for both to finish.
            await asyncio.gather(*tasks)
        except asyncio.CancelledError: # worker is killed
            # Handle the task cancellation gracefully
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            log.debug("Worker tasks cancelled.")


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
