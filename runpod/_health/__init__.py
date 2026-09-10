"""Worker-process identity shared by startup and health checks."""

import os
import sys

# Launchers may set this to the PID of the Python handler process before exec.
# A generic container-level boolean would also authorize unrelated processes.
WORKER_PID_ENV = "RUNPOD_FITNESS_WORKER_PID"


def is_worker_process() -> bool:
    """Require explicit launcher identity and exclude local/API test invocations."""
    return (
        os.environ.get(WORKER_PID_ENV) == str(os.getpid())
        and bool(os.environ.get("RUNPOD_WEBHOOK_GET_JOB"))
        and not any(
            arg.split("=", 1)[0] in ("--test_input", "--rp_serve_api")
            for arg in sys.argv[1:]
        )
    )
