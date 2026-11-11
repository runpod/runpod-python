"""execute project modules with their normal python import identities."""

import importlib
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Dict, List

from .app import App, _restore_registry, get_registered_apps
from .discovery_state import DISCOVERY_ENV
from .errors import InvalidResourceError
from .source import DISCOVERY_SKIP_DIRS, iter_source_files

log = logging.getLogger(__name__)

_loaded_modules: Dict[str, ModuleType] = {}


class DiscoveryError(Exception):
    """the project could not be loaded unambiguously."""


@dataclass(frozen=True)
class Project:
    target: Path
    source_root: Path
    import_root: Path


def resolve_project(target: Path) -> Project:
    target = target.resolve()
    if not target.exists():
        raise DiscoveryError(f"{target} does not exist")
    source_root = target if target.is_dir() else target.parent
    import_root = source_root
    while (import_root / "__init__.py").is_file():
        if not import_root.name.isidentifier():
            raise DiscoveryError(
                f"{import_root / '__init__.py'} makes {import_root} a package, "
                "but its directory name is not a valid python identifier. "
                "Rename the directory or remove __init__.py for a flat project."
            )
        source_root = import_root
        import_root = import_root.parent
    return Project(target, source_root, import_root)


def _python_files(project: Project) -> List[Path]:
    target = project.target
    if target.is_file():
        if target.suffix != ".py":
            raise DiscoveryError(f"{target} is not a python file")
        return [target]
    return sorted(
        path
        for path in iter_source_files(project.source_root, discovery=True)
        if path.is_relative_to(target)
    )


def _module_name(path: Path, import_root: Path) -> str:
    parts = list(path.relative_to(import_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    if not parts or any(not part.isidentifier() for part in parts):
        raise DiscoveryError(
            f"{path} does not have a valid python module name. "
            "Rename it or add it to .runpodignore if it is not application code."
        )
    return ".".join(parts)


def _project_modules(root: Path) -> Dict[str, ModuleType]:
    modules = {}
    for name, module in list(sys.modules.items()):
        if module is None or name == "runpod" or name.startswith("runpod."):
            continue
        source = getattr(module, "__file__", None)
        paths = [source] if source else getattr(module, "__path__", ())
        for path in paths:
            resolved = Path(path).resolve()
            if resolved.is_relative_to(root) and not any(
                part in DISCOVERY_SKIP_DIRS for part in resolved.relative_to(root).parts
            ):
                modules[name] = module
                break
    return modules


def _remove_modules(modules: Dict[str, ModuleType]) -> None:
    for name, module in modules.items():
        if sys.modules.get(name) is not module:
            continue
        if cached := getattr(module, "__cached__", None):
            Path(cached).unlink(missing_ok=True)
        sys.modules.pop(name, None)
        parent_name, _, child_name = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and getattr(parent, child_name, None) is module:
            delattr(parent, child_name)


def _rollback_modules(before: set, root: Path) -> None:
    _remove_modules(
        {
            name: module
            for name, module in _project_modules(root).items()
            if name not in before
        }
    )


def _import_module(path: Path, project: Project) -> None:
    name = _module_name(path, project.import_root)
    try:
        module = importlib.import_module(name)
        source = getattr(module, "__file__", None)
        if source is None or Path(source).resolve() != path:
            raise DiscoveryError(
                f"module {name!r} resolves to {source}, expected {path}. "
                "Rename the project module to avoid shadowing an installed "
                "or standard-library module."
            )
    except (Exception, SystemExit) as exc:
        hint = ""
        if isinstance(exc, ModuleNotFoundError) and (
            project.source_root != project.import_root
        ):
            hint = (
                f"Package context is set by {project.source_root / '__init__.py'}. "
                "Use package-relative imports for sibling modules, or remove "
                "that __init__.py if this is intended to be a flat project. "
            )
        raise DiscoveryError(
            f"importing {path} failed: {exc}\n{hint}"
            "If this file is not application code, add it to .runpodignore."
        ) from exc


def validate_apps(apps: List[App]) -> None:
    """validate the complete desired resource set before provisioning."""
    for app in apps:
        if not app.resources:
            raise InvalidResourceError(
                f"app {app.name!r} has no resources; check its resource modules and imports"
            )
        for handle in app.resources.values():
            handle.spec.validate()


def discover_apps(target: Path, *, strict: bool = False) -> List[App]:
    """execute a fresh project scan and collect its app registrations.

    strict scans and file targets abort on import errors. tolerant directory
    scans support inspection of projects with broken modules. unsuccessful
    scans restore the previous project modules and registrations.
    """
    global _loaded_modules
    project = resolve_project(target)
    files = _python_files(project)
    registered_before = get_registered_apps()
    modules_before = {
        name: module
        for name, module in _loaded_modules.items()
        if sys.modules.get(name) is module
    }
    modules_before.update(_project_modules(project.source_root))
    parent_bindings = []
    for name, module in modules_before.items():
        parent_name, _, child_name = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and getattr(parent, child_name, None) is module:
            parent_bindings.append((parent, child_name, module))
    _remove_modules(modules_before)
    _restore_registry(
        [
            app
            for app in registered_before
            if not (
                (source := getattr(app, "_source_file", None))
                and Path(source).resolve().is_relative_to(project.source_root)
            )
        ]
    )
    before = {id(app) for app in get_registered_apps()}
    importlib.invalidate_caches()
    import_path = str(project.import_root)
    sys.path.insert(0, import_path)
    previous_discovery = os.environ.get(DISCOVERY_ENV)
    os.environ[DISCOVERY_ENV] = "1"
    failures = []
    try:
        for path in files:
            registered = get_registered_apps()
            imported_before = set(sys.modules)
            try:
                _import_module(path, project)
            except BaseException as exc:
                _restore_registry(registered)
                _rollback_modules(imported_before, project.source_root)
                if (
                    not isinstance(exc, DiscoveryError)
                    or strict
                    or project.target.is_file()
                ):
                    raise
                failures.append(str(exc))
                log.warning("%s", exc)

        found = [app for app in get_registered_apps() if id(app) not in before]
        by_name = {}
        for app in found:
            if app.name in by_name and by_name[app.name] is not app:
                other = by_name[app.name]
                raise DiscoveryError(
                    f"distinct Apps named {app.name!r} are defined in "
                    f"{other._source_file} and {app._source_file}; "
                    "import one shared App or use distinct names"
                )
            by_name[app.name] = app
        if not found and failures:
            raise DiscoveryError(
                "no runpod.App found; some files failed to import:\n  "
                + "\n  ".join(failures)
            )
        if strict:
            if not found:
                raise DiscoveryError("no runpod.App found in the selected target")
            validate_apps(found)
        _loaded_modules = _project_modules(project.source_root)
        return found
    except BaseException:
        _remove_modules(_project_modules(project.source_root))
        sys.modules.update(modules_before)
        for parent, child_name, module in parent_bindings:
            setattr(parent, child_name, module)
        _restore_registry(registered_before)
        raise
    finally:
        for index, entry in enumerate(sys.path):
            if entry is import_path:
                del sys.path[index]
                break
        if previous_discovery is None:
            os.environ.pop(DISCOVERY_ENV, None)
        else:
            os.environ[DISCOVERY_ENV] = previous_discovery
