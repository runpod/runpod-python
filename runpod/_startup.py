"""Process-scoped startup gate; safe to import without loading serverless."""

import sys

from ._health import WORKER_PID_ENV, is_worker_process

__all__ = ["WORKER_PID_ENV", "is_worker_process", "run_import_checks"]


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
