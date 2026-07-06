"""module discovery for `rp deploy` and `rp dev`.

imports target modules under __name__ != "__main__" (so main guards
never run) and collects App instances from the registry. a per-module
import timeout guards against module-level code that blocks.
"""

import importlib.util
import logging
import os
import sys
import threading
from pathlib import Path
from typing import List

from .app import App, get_registered_apps

log = logging.getLogger(__name__)

IMPORT_TIMEOUT_SECONDS = 30

# set while discovery imports modules; handle invocation checks it so
# module-level .remote()/.spawn() calls fail fast with a precise
# diagnosis instead of attempting network calls mid-scan
DISCOVERY_ENV = "RUNPOD_DISCOVERY_SCAN"


def in_discovery() -> bool:
    return bool(os.environ.get(DISCOVERY_ENV))


class DiscoveryInvocationError(Exception):
    """a handle was invoked at module level during a discovery scan."""

    def __init__(self, resource_name: str):
        super().__init__(
            f"'{resource_name}' was invoked at import time. move calls "
            f"into a function, an entrypoint, or an "
            f'`if __name__ == "__main__":` guard so importing the file '
            f"has no side effects."
        )

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".runpod",
    ".flash",
    "build",
    "dist",
}


class DiscoveryError(Exception):
    """a module failed to import during discovery."""


def _python_files(target: Path) -> List[Path]:
    if target.is_file():
        if target.suffix != ".py":
            raise DiscoveryError(f"{target} is not a python file")
        return [target]
    files = []
    for path in sorted(target.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        files.append(path)
    return files


def _import_module(path: Path) -> None:
    """import one file as a uniquely-named module, never as __main__."""
    module_name = f"_runpod_discovered_{path.stem}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise DiscoveryError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    error: List[BaseException] = []

    def run() -> None:
        try:
            spec.loader.exec_module(module)
        except BaseException as exc:  # noqa: BLE001 - reported to caller
            error.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(IMPORT_TIMEOUT_SECONDS)

    if thread.is_alive():
        raise DiscoveryError(
            f"importing {path} timed out after {IMPORT_TIMEOUT_SECONDS}s; "
            f"module-level code must not block (guard it with "
            f'`if __name__ == "__main__":` or runpod.is_local())'
        )
    if error:
        raise DiscoveryError(f"importing {path} failed: {error[0]}") from error[0]


def discover_apps(target: Path) -> List[App]:
    """import python files under target and return the apps they define.

    a single-file target imports strictly and raises on any failure.
    a directory walk is tolerant: files that fail to import are
    collected as warnings, and discovery only fails outright when no
    app was found anywhere (the failures are then the likely cause and
    are included in the error).
    """
    before = set(id(a) for a in get_registered_apps())

    sys_path_added = False
    root = target if target.is_dir() else target.parent
    root_str = str(root.resolve())
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
        sys_path_added = True

    strict = target.is_file()
    failures: List[str] = []
    os.environ[DISCOVERY_ENV] = "1"
    try:
        for path in _python_files(target):
            seen = set(id(a) for a in get_registered_apps())
            try:
                _import_module(path)
            except DiscoveryError as exc:
                if strict:
                    raise
                failures.append(str(exc))
                log.warning("%s", exc)
                continue
            # stamp fresh apps with their defining file so callers can
            # report where each app came from
            for app in get_registered_apps():
                if id(app) not in seen and not hasattr(app, "_source_file"):
                    app._source_file = path
    finally:
        os.environ.pop(DISCOVERY_ENV, None)
        if sys_path_added:
            sys.path.remove(root_str)

    found = [a for a in get_registered_apps() if id(a) not in before]
    # a file imported both by the scan (synthetic name) and by another
    # file (real name, e.g. `from main import q1`) registers its app
    # twice; deploying duplicates would be wasteful and confusing
    by_name = {}
    for app in found:
        by_name.setdefault(app.name, app)
    found = list(by_name.values())
    if not found and failures:
        raise DiscoveryError(
            "no runpod.App found; some files failed to import:\n  "
            + "\n  ".join(failures)
        )
    return found
