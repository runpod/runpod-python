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
from typing import Any

from ..version import __version__ as runpod_version
from . import worker
from .modules.rp_fitness import register_fitness_check
from .modules.rp_logger import RunPodLogger
from .modules.rp_prestart import has_prestart_hooks as _has_prestart_hooks
from .modules.rp_prestart import register_prestart_hook
from .modules.rp_progress import progress_update
from .utils.rp_volume_cache import VolumeCache

__all__ = [
    "VolumeCache",
    "progress_update",
    "register_fitness_check",
    "register_prestart_hook",
    "runpod_version",
    "start",
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


def _set_config_args(config: dict[str, Any]) -> dict[str, Any]:
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


def _validate_prestart_mode(config: dict[str, Any], realtime_port: int) -> None:
    """Check whether registered hooks have a safe adapter for the selected mode.

    Queue and local-input modes are single-process SDK lifecycles. Hosted API
    mode is supported only with one Uvicorn worker so the hook runs exactly once
    in the same process as the handler. Realtime is rejected because its worker
    cardinality, readiness, and persistent-connection failure contract are not
    defined for prestart hooks.
    """
    if not _has_prestart_hooks():
        return

    if config["rp_args"]["rp_serve_api"]:
        if config["rp_args"]["rp_api_concurrency"] != 1:
            raise RuntimeError(
                "Prestart hooks require rp_api_concurrency=1 in hosted API mode."
            )
        return

    if realtime_port:
        raise RuntimeError("Prestart hooks are not supported in realtime mode.")


# ---------------------------------------------------------------------------- #
#                            Start Serverless Worker                           #
# ---------------------------------------------------------------------------- #
def start(config: dict[str, Any]):
    """
    Starts the serverless worker.

    config (dict[str, Any]): Configuration parameters for the worker.

    config["handler"] (Callable): The handler function to run.

    config["rp_args"] (dict[str, Any]): Arguments populated by runtime arguments.

    Prestart hooks registered with `register_prestart_hook` run once before
    handler execution in queue-based, local test, and hosted API modes.
    Production queue intake continues while hooks run; local and hosted API
    handlers do not accept work until every hook finishes.

    config["prestart_timeout"] (int, optional): Seconds allowed for the complete
        prestart phase. Omit for no timeout.
    """
    print(f"--- Starting Serverless Worker |  Version {runpod_version} ---")

    signal.signal(signal.SIGINT, _signal_handler)

    config["reference_counter_start"] = time.perf_counter()
    config = _set_config_args(config)

    realtime_port = _get_realtime_port()
    realtime_concurrency = _get_realtime_concurrency()

    _validate_prestart_mode(config, realtime_port)

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
