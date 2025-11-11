"""
Contains the main entrypoint for the Runpod Serverless Worker.

Arguments can be passed in when the worker is started, and will be passed to the worker.
"""

import argparse
import json
import os
import signal
import sys
import time
from typing import Any, Dict

from ..version import __version__ as runpod_version
from . import worker
from .modules.rp_logger import RunPodLogger
from .modules.rp_progress import progress_update

__all__ = [
    "start",
    "progress_update", 
    "runpod_version"
]

log = RunPodLogger()


# ---------------------------------------------------------------------------- #
#                              Run Time Arguments                              #
# ---------------------------------------------------------------------------- #
# Arguments will be passed in with the config under the key "rp_args"
parser = argparse.ArgumentParser(
    prog="runpod", description="Runpod Serverless Worker Arguments."
)
parser.add_argument(
    "--rp_log_level",
    type=str,
    default=None,
    help="""Controls what level of logs are printed to the console.
                    Options: ERROR, WARN, INFO, and DEBUG.""",
)

parser.add_argument(
    "--rp_debugger",
    action="store_true",
    default=None,
    help="Flag to enable the Debugger.",
)

# Hosted API
parser.add_argument(
    "--rp_serve_api",
    action="store_true",
    default=None,
    help="Flag to start the API server.",
)
parser.add_argument(
    "--rp_api_port", type=int, default=8000, help="Port to start the FastAPI server on."
)
parser.add_argument(
    "--rp_api_concurrency",
    type=int,
    default=1,
    help="Number of concurrent FastAPI workers.",
)
parser.add_argument(
    "--rp_api_host",
    type=str,
    default="localhost",
    help="Host to start the FastAPI server on.",
)

# Test input
parser.add_argument(
    "--test_input",
    type=str,
    default=None,
    help="Test input for the worker, formatted as JSON.",
)


def _set_config_args(config) -> dict:
    """
    Sets the config rp_args, removing any recognized arguments from sys.argv.
    Returns: config
    """
    args, unknown = parser.parse_known_args()
    sys.argv = [sys.argv[0]] + unknown

    # Directly assign the parsed arguments to config
    config["rp_args"] = vars(args)

    # Parse the test input from JSON
    if config["rp_args"]["test_input"]:
        config["rp_args"]["test_input"] = json.loads(config["rp_args"]["test_input"])

    # Parse the test output from JSON
    if config["rp_args"].get("test_output", None):
        config["rp_args"]["test_output"] = json.loads(config["rp_args"]["test_output"])

    # Set the log level
    if config["rp_args"]["rp_log_level"]:
        log.set_level(config["rp_args"]["rp_log_level"])

    return config


def _get_realtime_port() -> int:
    """
    Get the realtime port from the environment variable if it exists.
    """
    return int(os.environ.get("RUNPOD_REALTIME_PORT", "0"))


def _get_realtime_concurrency() -> int:
    """
    Get the realtime concurrency from the environment variable if it exists.
    """
    return int(os.environ.get("RUNPOD_REALTIME_CONCURRENCY", "1"))


def _signal_handler(sig, frame):
    """
    Handles the SIGINT signal.
    """
    del sig, frame
    log.info("SIGINT received. Shutting down.")
    sys.exit(0)


# ---------------------------------------------------------------------------- #
#                            Start Serverless Worker                           #
# ---------------------------------------------------------------------------- #
def start(config: Dict[str, Any]):
    """
    Starts the serverless worker.

    config (Dict[str, Any]): Configuration parameters for the worker.

    config["handler"] (Callable): The handler function to run.

    config["rp_args"] (Dict[str, Any]): Arguments for the worker, populated by runtime arguments.
    """
    print(f"--- Starting Serverless Worker |  Version {runpod_version} ---")

    signal.signal(signal.SIGINT, _signal_handler)

    config["reference_counter_start"] = time.perf_counter()
    config = _set_config_args(config)

    realtime_port = _get_realtime_port()
    realtime_concurrency = _get_realtime_concurrency()

    if config["rp_args"]["rp_serve_api"]:
        log.info("Starting API server.")
        from .modules import rp_fastapi
        api_server = rp_fastapi.WorkerAPI(config)

        api_server.start_uvicorn(
            api_host=config["rp_args"]["rp_api_host"],
            api_port=config["rp_args"]["rp_api_port"],
            api_concurrency=config["rp_args"]["rp_api_concurrency"],
        )
        return

    if realtime_port:
        log.info(f"Starting API server for realtime on port {realtime_port}.")
        from .modules import rp_fastapi
        api_server = rp_fastapi.WorkerAPI(config)

        api_server.start_uvicorn(
            api_host="0.0.0.0",
            api_port=realtime_port,
            api_concurrency=realtime_concurrency,
        )
        return

    worker.main(config)
    return
