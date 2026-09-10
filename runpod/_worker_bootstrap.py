"""Launch a worker with health checks before executing its handler module.

Usage: runpod-worker handler.py [handler arguments]
       runpod-worker -m package.handler [handler arguments]
"""

import os
import runpy
import sys
from pathlib import Path

from ._startup import WORKER_PID_ENV, run_import_checks


def main() -> None:
    """Select this process as the worker, then execute the unmodified handler."""
    args = sys.argv[1:]
    module_mode = bool(args and args[0] == "-m")
    if module_mode:
        args = args[1:]
    if not args or args[0].startswith("-"):
        raise SystemExit("Usage: runpod-worker [-m] handler [arguments]")
    target, *handler_args = args
    sys.argv = [target, *handler_args]
    if not module_mode:
        # Match `python handler.py`: sibling imports resolve beside the script.
        target = str(Path(target).resolve())
        if not Path(target).is_file():
            raise SystemExit(f"Worker handler not found: {target}")
        sys.path.insert(0, str(Path(target).parent))
    else:
        # Console entrypoints put their bin directory on sys.path, unlike python -m.
        sys.path.insert(0, os.getcwd())
    os.environ[WORKER_PID_ENV] = str(os.getpid())
    try:
        run_import_checks()
    finally:
        # Helpers, subprocesses and multiprocessing children are not workers.
        os.environ.pop(WORKER_PID_ENV, None)
    if module_mode:
        runpy.run_module(target, run_name="__main__", alter_sys=True)
    else:
        runpy.run_path(target, run_name="__main__")


if __name__ == "__main__":
    main()
