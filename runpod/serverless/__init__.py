"""Allows serverless to be recognized as a package."""

import os
import sys
import json
import asyncio
import argparse

from . import work_loop
from .modules import rp_fastapi

# Grab runtime arguments
parser = argparse.ArgumentParser()
parser.add_argument("--test_input", type=str, default=None,
                    help="Test input for the worker, formatted as JSON.")


def _get_realtime_port() -> int:
    port = os.environ.get("RUNPOD_REALTIME_PORT", None)
    if port:
        return int(port)
    return None


def _get_realtime_concurrency() -> int:
    concurrency = os.environ.get("RUNPOD_REALTIME_CONCURRENCY", 1)
    return int(concurrency)


def start(config):
    """
    Starts the serverless worker.
    """
    args, unknown = parser.parse_known_args()

    # Modify sys.argv to remove the recognized arguments
    sys.argv = [sys.argv[0]] + unknown

    # Set test input, if provided
    if args.test_input is not None:
        config["test_input"] = json.loads(args.test_input)

    realtime_port = _get_realtime_port()
    realtime_concurrency = _get_realtime_concurrency()

    if realtime_port:
        api_server = rp_fastapi.WorkerAPI()
        api_server.config = config

        api_server.start_uvicorn(realtime_port, realtime_concurrency)

    else:
        asyncio.run(work_loop.start_worker(config))
