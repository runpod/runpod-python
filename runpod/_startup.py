"""Container-scoped startup gate; safe to import without loading serverless."""

import sys

from ._health import is_early_check_eligible

__all__ = ["is_early_check_eligible", "run_import_checks"]


def run_import_checks() -> None:
    """Run shared early checks in eligible Serverless containers."""
    if not is_early_check_eligible():
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
