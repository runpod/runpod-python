"""Process-scoped startup gate; safe to import without loading serverless."""

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


def run_import_checks() -> None:
    """Run early checks only in the handler process selected by the launcher."""
    if not is_worker_process():
        return
    try:
        from ._health.fitness import run_startup_fitness_checks

        run_startup_fitness_checks()
    except Exception as exc:
        # Import/configuration errors are retried through the worker-start path.
        # Actual failed checks force-exit and do not pass through this handler.
        print(
            f"Runpod startup checks could not be prepared: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
