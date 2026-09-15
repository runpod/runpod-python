"""module discovery for `rp flash deploy` and `rp flash dev`.

imports target modules under __name__ != "__main__" (so main guards
never run) and collects App instances from the registry. imports run on
the caller thread so discovery never abandons executing module code.
"""

import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import List

from .app import App, _restore_registry, get_registered_apps
from .discovery_state import DISCOVERY_ENV

log = logging.getLogger(__name__)


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


def _module_name(path: Path) -> str:
    parts = [] if path.name == "__init__.py" else [path.stem]
    parent = path.parent
    while (parent / "__init__.py").is_file():
        parts.insert(0, parent.name)
        parent = parent.parent
    if parent != path.parent:
        return ".".join(parts)
    return f"_runpod_discovered_{path.stem}_{abs(hash(str(path)))}"


def _rollback_modules(before: set, root: Path) -> None:
    for name in set(sys.modules) - before:
        module = sys.modules.get(name)
        source = getattr(module, "__file__", None)
        if not source or not Path(source).resolve().is_relative_to(root):
            continue
        sys.modules.pop(name, None)
        parent_name, _, child_name = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and getattr(parent, child_name, None) is module:
            delattr(parent, child_name)


def _import_module(path: Path) -> None:
    """import a file in its package context, never as __main__."""
    module_name = _module_name(path)
    previous_module = sys.modules.get(module_name)
    try:
        if (path.parent / "__init__.py").is_file():
            module = importlib.import_module(module_name)
            if Path(module.__file__).resolve() != path:
                raise DiscoveryError(
                    f"module {module_name!r} is already loaded from {module.__file__}"
                )
        else:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise DiscoveryError(f"cannot load {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
    except BaseException as exc:
        if previous_module is not None:
            sys.modules[module_name] = previous_module
        else:
            sys.modules.pop(module_name, None)
        if isinstance(exc, (Exception, SystemExit)):
            raise DiscoveryError(f"importing {path} failed: {exc}") from exc
        raise


def discover_apps(target: Path) -> List[App]:
    """import python files under target and return the apps they define.

    a single-file target imports strictly and raises on any failure.
    a directory walk is tolerant: files that fail to import are
    collected as warnings, and discovery only fails outright when no
    app was found anywhere (the failures are then the likely cause and
    are included in the error).
    """
    target = target.resolve()
    before = set(id(a) for a in get_registered_apps())

    local_root = target if target.is_dir() else target.parent
    root = local_root
    while (root / "__init__.py").is_file():
        root = root.parent
    added_paths = []
    for import_root in (local_root, root):
        root_str = str(import_root)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
            added_paths.append(root_str)

    strict = target.is_file()
    failures: List[str] = []
    previous_discovery = os.environ.get(DISCOVERY_ENV)
    os.environ[DISCOVERY_ENV] = "1"
    try:
        for path in _python_files(target):
            registered = get_registered_apps()
            seen = {id(a) for a in registered}
            modules_before = set(sys.modules)
            imported = False
            try:
                try:
                    _import_module(path)
                    imported = True
                finally:
                    if not imported:
                        _restore_registry(registered)
                        _rollback_modules(modules_before, root)
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
        if previous_discovery is None:
            os.environ.pop(DISCOVERY_ENV, None)
        else:
            os.environ[DISCOVERY_ENV] = previous_discovery
        for root_str in added_paths:
            sys.path.remove(root_str)

    found = [a for a in get_registered_apps() if id(a) not in before]

    # dedupe
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
