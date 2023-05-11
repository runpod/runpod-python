"""Allows serverless to be recognized as a package."""

import os
import json
import asyncio
import argparse

from . import work_loop
from .modules import rp_fastapi

# Grab runtime arguments
parser = argparse.ArgumentParser()
parser.add_argument("--test_input", type=str, default=None)


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
    args = parser.parse_args()

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
