"""decide which resources call other resources from inside a worker.

workers authenticate nested .remote() calls with the account api key, so
deploy and dev give the key only to resources that may make one. the
analysis reads the resource's module source and errs toward the key: a
resource gets it when its source is unavailable, when it is defined
inside another function, or when it uses runpod or project code that the
analysis does not follow.
"""

import ast
import functools
import importlib.util
import inspect
import logging
import site
import sys
import sysconfig
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

_REMOTE_ATTRS = frozenset({"remote", "spawn"})
_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def resource_target(handle):
    """the user function or class behind a resource handle."""
    return (
        getattr(handle, "_fn", None)
        or getattr(handle, "_cls", None)
        or getattr(handle, "__wrapped__", None)
    )


def may_call_resources(app, spec) -> bool:
    """true unless the resource named by spec provably makes no remote calls."""
    handle = app.resources.get(spec.name)
    return handle is None or makes_remote_calls(app, handle)


def _top_module(module_name: str) -> str:
    return module_name.split(".")[0]


def _is_module_level(fn) -> bool:
    """true for a plain module-level definition the analysis can find."""
    name = getattr(fn, "__name__", None)
    return name is not None and getattr(fn, "__qualname__", name) == name


class _BodyNames(ast.NodeVisitor):
    """names, attributes, and imports used in a definition's body, not
    its decorators."""

    def __init__(self):
        self.names: Set[str] = set()
        self.attrs: Set[str] = set()
        # (module, relative import level)
        self.imports: Set[Tuple[str, int]] = set()

    def visit_FunctionDef(self, node):
        for default in node.args.defaults + node.args.kw_defaults:
            if default is not None:
                self.visit(default)
        for stmt in node.body:
            self.visit(stmt)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        for base in node.bases:
            self.visit(base)
        for stmt in node.body:
            self.visit(stmt)

    def visit_Lambda(self, node):
        self.visit(node.body)

    def visit_Name(self, node):
        self.names.add(node.id)

    def visit_Attribute(self, node):
        self.attrs.add(node.attr)
        self.generic_visit(node)

    def visit_Import(self, node):
        self.imports.update((alias.name, 0) for alias in node.names)

    def visit_ImportFrom(self, node):
        self.imports.add((node.module or "", node.level))


def _body_names(node: ast.AST) -> _BodyNames:
    visitor = _BodyNames()
    if isinstance(node, _DEF_NODES):
        visitor.visit(node)
    else:
        visitor.generic_visit(node)
    return visitor


@functools.lru_cache(maxsize=1)
def _installed_dirs() -> Tuple[Path, ...]:
    """directories holding the stdlib and installed distributions."""
    paths = {
        sysconfig.get_paths().get(key)
        for key in ("stdlib", "platstdlib", "purelib", "platlib")
    }
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


def _is_installed(path: str) -> bool:
    resolved = Path(path).resolve()
    return any(resolved.is_relative_to(d) for d in _installed_dirs())


def _in_project(top: str, project_dirs: Tuple[Path, ...]) -> bool:
    return any((d / f"{top}.py").is_file() or (d / top).is_dir() for d in project_dirs)


def _is_stdlib(top: str) -> bool:
    return top in sys.builtin_module_names or top in getattr(
        sys, "stdlib_module_names", ()
    )


def _resolves_outside_installed(top: str) -> bool:
    """true when the module resolves outside the stdlib and installed
    distributions, or fails to resolve. a module found nowhere (torch
    imported inside a gpu function, absent from the client) is a
    third-party package, so it does not count."""
    try:
        spec = importlib.util.find_spec(top)
    except (ImportError, ValueError):
        return True
    if spec is None:
        return False
    locations = [spec.origin] if spec.origin and spec.has_location else []
    locations += list(spec.submodule_search_locations or [])
    return not all(_is_installed(loc) for loc in locations)


def _is_local(module_name: str, project_dirs: Tuple[Path, ...]) -> bool:
    """true unless the module is the stdlib or an installed distribution.
    local modules are not followed, so a resource using one gets the key."""
    top = _top_module(module_name)
    if not top or _in_project(top, project_dirs):
        return True
    return not _is_stdlib(top) and _resolves_outside_installed(top)


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
    module_globals = vars(module).items() if module else ()
    names.update(
        key
        for key, value in module_globals
        if isinstance(value, (FunctionHandle, ApiHandle)) and value is not handle
    )
    names.discard(None)
    return names


@dataclass
class _ModuleIndex:
    """a module's top-level definitions, assignments, and the names it
    imports from runpod or project code."""

    project_dirs: Tuple[Path, ...]
    defs: Dict[str, ast.AST] = field(default_factory=dict)
    assigns: Dict[str, ast.AST] = field(default_factory=dict)
    call_capable: Set[str] = field(default_factory=set)

    def import_may_call(self, module_name: str, level: int) -> bool:
        """true for runpod and project imports, which can reach a resource."""
        return (
            bool(level)
            or _top_module(module_name) == "runpod"
            or _is_local(module_name, self.project_dirs)
        )

    def add(self, node: ast.stmt) -> None:
        if isinstance(node, _DEF_NODES):
            self.defs[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            self.assigns.update(dict.fromkeys(_assigned_names(node), node))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            self.call_capable.update(self._call_capable_imports(node))

    def _call_capable_imports(self, node) -> List[str]:
        """names an import binds when it comes from runpod or project code."""
        if isinstance(node, ast.Import):
            return [
                alias.asname or _top_module(alias.name)
                for alias in node.names
                if self.import_may_call(alias.name, 0)
            ]
        if not self.import_may_call(node.module or "", node.level):
            return []
        return [alias.asname or alias.name for alias in node.names]

    def node(self, name: str) -> ast.AST:
        return self.defs.get(name) or self.assigns[name]

    def helpers(self, names: Set[str]) -> List[str]:
        """the names that refer to module-level definitions or values."""
        return [n for n in names if n in self.defs or n in self.assigns]


def _assigned_names(node) -> List[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]


def _source_tree(path: Optional[str]) -> Optional[ast.Module]:
    try:
        return ast.parse(Path(path).read_text(encoding="utf-8")) if path else None
    except (OSError, SyntaxError, ValueError):
        return None


def _uses_resource(
    used: _BodyNames, index: _ModuleIndex, other_resources: Set[str]
) -> bool:
    """true if a body calls .remote()/.spawn(), names another resource, or
    uses runpod or project code."""
    return bool(
        used.attrs & _REMOTE_ATTRS
        or used.names & (other_resources | index.call_capable)
        or any(index.import_may_call(m, level) for m, level in used.imports)
    )


def _reaches_resource(index: _ModuleIndex, roots, other_resources: Set[str]) -> bool:
    """true if the roots, or any module-level helper they use, use a resource."""
    seen: Set[str] = set()
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        used = _body_names(index.node(name))
        if _uses_resource(used, index, other_resources):
            return True
        pending.extend(index.helpers(used.names - seen))
    return False


def _worker_entry_points(handle) -> Optional[List[Callable]]:
    """the functions a worker runs for this resource: its target and any
    startup hook. None when one is nested or anonymous, since the
    analysis cannot find it in the module source."""
    target = resource_target(handle)
    init_fn = getattr(handle, "_init_fn", None)
    entry_points = [fn for fn in (target, init_fn) if fn is not None]
    if target is None or not all(_is_module_level(fn) for fn in entry_points):
        return None
    return entry_points


def _index_module(fn: Callable) -> Optional[_ModuleIndex]:
    """index the module that defines fn, or None without its source."""
    try:
        path = inspect.getsourcefile(fn)
    except TypeError:  # builtins and c extensions
        path = None
    tree = _source_tree(path)
    if tree is None:
        log.debug("source for %s unavailable; giving it the api key", fn.__name__)
        return None
    index = _ModuleIndex(_project_dirs(path))
    for node in tree.body:
        index.add(node)
    return index


def makes_remote_calls(app, handle) -> bool:
    """true unless the resource provably cannot reach another resource.

    whenever the analysis cannot follow the resource, it answers true, so
    a worker that needs the api key never goes without it.
    """
    entry_points = _worker_entry_points(handle)
    if entry_points is None:
        return True

    target = entry_points[0]
    index = _index_module(target)
    names = [fn.__name__ for fn in entry_points]
    if index is None or not all(name in index.defs for name in names):
        return True

    other_resources = _resource_names(target, app, handle)
    return _reaches_resource(index, names, other_resources)
