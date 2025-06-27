"""
runpod | serverless | worker_loop.py
Called to convert a container into a worker pod for the runpod serverless platform.
"""

import asyncio
import os
from typing import Any, Dict

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
def run_worker(config: Dict[str, Any]) -> None:
    """
    Starts the worker loop for multi-processing.

    This function is called when the worker is running on Runpod. This function
    starts a loop that runs indefinitely until the worker is killed.

    Args:
        config (Dict[str, Any]): Configuration parameters for the worker.
    """
    # Start pinging Runpod to show that the worker is alive.
    heartbeat.start_ping()

    # Create a JobScaler responsible for adjusting the concurrency
    job_scaler = rp_scale.JobScaler(config)
    job_scaler.start()


def main(config: Dict[str, Any]) -> None:
    """
    Checks if the worker is running locally or on Runpod.
    If running locally, the test job is run and the worker exits.
    If running on Runpod, the worker loop is created.
    """
    if _is_local(config):
        asyncio.run(rp_local.run_local(config))

    else:
        run_worker(config)
