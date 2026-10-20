"""decide which resources call other resources from inside a worker.

workers authenticate nested .remote() calls with the account api key, so
deploy and dev give the key to every resource that may make one. the
analysis reads the resource's module source and fails open: anything it
cannot follow counts as a remote call, so no worker that needs the key
goes without it.
"""

import ast
import importlib.util
import inspect
import logging
import site
import sys
import sysconfig
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

log = logging.getLogger(__name__)

# handle methods that start a call on another resource
_REMOTE_ATTRS = frozenset({"remote", "spawn"})


def resource_target(handle):
    """the user function or class behind a resource handle."""
    return (
        getattr(handle, "_fn", None)
        or getattr(handle, "_cls", None)
        or getattr(handle, "__wrapped__", None)
    )


class _BodyNames(ast.NodeVisitor):
    """names and attributes used in a definition's body, not its decorators."""

    def __init__(self):
        self.names: Set[str] = set()
        self.attrs: Set[str] = set()
        # (module, level) of imports inside the body
        self.imports: Set[Tuple[str, int]] = set()

    def visit_FunctionDef(self, node):
        self._visit_def(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        for base in node.bases:
            self.visit(base)
        for stmt in node.body:
            self.visit(stmt)

    def visit_Lambda(self, node):
        self.visit(node.body)

    def _visit_def(self, node):
        for default in node.args.defaults + node.args.kw_defaults:
            if default is not None:
                self.visit(default)
        for stmt in node.body:
            self.visit(stmt)

    def visit_Name(self, node):
        self.names.add(node.id)

    def visit_Attribute(self, node):
        self.attrs.add(node.attr)
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            self.imports.add((alias.name, 0))

    def visit_ImportFrom(self, node):
        self.imports.add((node.module or "", node.level))


def _body_names(node) -> _BodyNames:
    visitor = _BodyNames()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        visitor.visit(node)
    else:
        visitor.generic_visit(node)
    return visitor


def _installed_dirs() -> Tuple[Path, ...]:
    """directories holding the stdlib and installed distributions."""
    paths = {sysconfig.get_paths().get(k) for k in ("stdlib", "platstdlib", "purelib", "platlib")}
    try:
        paths.update(site.getsitepackages())
    except AttributeError:  # some virtualenv site modules lack it
        pass
    paths.add(site.getusersitepackages())
    return tuple(Path(p).resolve() for p in paths if p)


def _project_dirs(module_file: str) -> Tuple[Path, ...]:
    """where the module's project-local imports live: its directory, the
    directory above its package, and the working directory."""
    here = Path(module_file).resolve().parent
    top = here
    while (top / "__init__.py").exists() and top.parent != top:
        top = top.parent
    return tuple(dict.fromkeys((here, top, Path.cwd().resolve())))


def _is_local(module_name: str, project_dirs: Tuple[Path, ...]) -> bool:
    """true unless the module is the stdlib or an installed distribution.

    project modules, and modules that resolve outside the installed
    locations, count as local: the analysis does not follow them. a
    module found nowhere (torch imported inside a gpu function, absent
    from the client) is a third-party package, not project code.
    """
    top = module_name.split(".")[0]
    if not top:
        return True
    if any((d / f"{top}.py").is_file() or (d / top).is_dir() for d in project_dirs):
        return True
    if top in sys.builtin_module_names or top in getattr(sys, "stdlib_module_names", ()):
        return False
    try:
        spec = importlib.util.find_spec(top)
    except (ImportError, ValueError):
        return True
    if spec is None:
        return False
    locations = [spec.origin] if spec.origin and spec.has_location else []
    locations += list(spec.submodule_search_locations or [])
    installed = _installed_dirs()
    return any(
        not any(Path(loc).resolve().is_relative_to(d) for d in installed)
        for loc in locations
    )


def _resource_names(target, app, handle) -> Set[str]:
    """names bound to resource handles other than this one: the app's
    resources and any handle in the module's globals (other apps)."""
    from .handles import ApiHandle, FunctionHandle

    names = {
        getattr(resource_target(h), "__name__", None)
        for h in app.resources.values()
        if h is not handle
    }
    module = inspect.getmodule(target)
    for key, value in vars(module).items() if module else ():
        if isinstance(value, (FunctionHandle, ApiHandle)) and value is not handle:
            names.add(key)
    names.discard(None)
    return names


def _module_tree(target) -> Optional[ast.Module]:
    try:
        path = inspect.getsourcefile(target)
        return ast.parse(Path(path).read_text(encoding="utf-8")) if path else None
    except (OSError, TypeError, SyntaxError, ValueError):
        return None


def makes_remote_calls(app, handle) -> bool:
    """true unless the resource provably cannot reach another resource."""
    target = resource_target(handle)
    name = getattr(target, "__name__", None)
    if target is None or getattr(target, "__qualname__", name) != name:
        # nested or anonymous definitions are not analyzed
        return True
    tree = _module_tree(target)
    if tree is None:
        log.debug("source for %s unavailable; giving it the api key", name)
        return True

    defs: Dict[str, ast.AST] = {}
    assigns: Dict[str, ast.AST] = {}
    runpod_names: Set[str] = set()
    local_imports: Set[str] = set()
    project_dirs = _project_dirs(inspect.getsourcefile(target))

    def _is_local_import(module_name: str, level: int) -> bool:
        return bool(level) or _is_local(module_name, project_dirs)

    def _uncertain_import(module_name: str, level: int) -> bool:
        return module_name.split(".")[0] == "runpod" or _is_local_import(module_name, level)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        assigns[n.id] = node
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if alias.name.split(".")[0] == "runpod":
                    runpod_names.add(bound)
                elif _is_local_import(alias.name, 0):
                    local_imports.add(bound)
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            for alias in node.names:
                bound = alias.asname or alias.name
                if module_name.split(".")[0] == "runpod":
                    runpod_names.add(bound)
                elif _is_local_import(module_name, node.level):
                    local_imports.add(bound)

    # worker startup hooks run on the worker too
    roots = [name]
    init_fn = getattr(handle, "_init_fn", None)
    if init_fn is not None:
        init_name = getattr(init_fn, "__name__", None)
        if getattr(init_fn, "__qualname__", init_name) != init_name:
            return True
        roots.append(init_name)
    if any(root not in defs for root in roots):
        return True

    other_resources = _resource_names(target, app, handle)

    seen: Set[str] = set()
    pending = list(roots)
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        used = _body_names(defs.get(current) or assigns[current])
        if used.attrs & _REMOTE_ATTRS:
            return True
        if any(_uncertain_import(m, level) for m, level in used.imports):
            return True
        for ref in used.names:
            if ref in other_resources or ref in runpod_names or ref in local_imports:
                return True
            if ref in seen:
                continue
            if ref in defs or ref in assigns:
                pending.append(ref)
    return False
