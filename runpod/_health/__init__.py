"""Lightweight Serverless environment detection; no SDK imports."""

import os
import sys


def is_serverless_environment() -> bool:
    """Recognize production worker configuration, excluding platform tests."""
    return (
        bool(os.environ.get("RUNPOD_ENDPOINT_ID", "").strip())
        and bool(os.environ.get("RUNPOD_WEBHOOK_GET_JOB", "").strip())
        and os.environ.get("RUNPOD_TEST", "").strip().lower()
        not in ("1", "true", "yes", "on")
    )


def is_worker_process() -> bool:
    """Eligibility for shared early checks, not an assertion of process identity."""
    return is_serverless_environment() and not any(
        arg.split("=", 1)[0] in ("--test_input", "--rp_serve_api")
        for arg in sys.argv[1:]
    )
