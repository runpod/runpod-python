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


def is_early_check_eligible() -> bool:
    """Eligibility for shared early checks, not an assertion of process identity."""
    # Realtime workers start an API server and never enter run_worker, where
    # fitness checks have historically run. Preserve that behavior at import.
    if os.environ.get("RUNPOD_REALTIME_PORT", "0").strip() not in ("", "0"):
        return False
    return is_serverless_environment() and not any(
        arg.split("=", 1)[0] in ("--test_input", "--rp_serve_api")
        for arg in sys.argv[1:]
    )
