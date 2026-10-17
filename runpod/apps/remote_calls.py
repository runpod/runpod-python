"""decide which resources call other resources from inside a worker.

workers authenticate nested .remote() calls with the account api key, so
deploy and dev give the key to every resource that may make one. the
analysis reads the resource's module source and fails open: anything it
cannot follow counts as a remote call, so no worker that needs the key
goes without it.
"""

import ast
import inspect
import logging
from pathlib import Path
from typing import Dict, Optional, Set

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


def _body_names(node) -> _BodyNames:
    visitor = _BodyNames()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        visitor.visit(node)
    else:
        visitor.generic_visit(node)
    return visitor


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
    module_file = inspect.getsourcefile(target) or ""
    project_dir = Path(module_file).resolve().parent if module_file else None

    def _is_local(module_name: str) -> bool:
        if not module_name or project_dir is None:
            return True
        top = module_name.split(".")[0]
        return (project_dir / f"{top}.py").exists() or (project_dir / top).is_dir()

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
                elif _is_local(alias.name):
                    local_imports.add(bound)
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            for alias in node.names:
                bound = alias.asname or alias.name
                if module_name.split(".")[0] == "runpod":
                    runpod_names.add(bound)
                elif node.level or _is_local(module_name):
                    local_imports.add(bound)

    if name not in defs:
        return True

    other_resources = {
        resource_target(h).__name__
        for h in app.resources.values()
        if h is not handle and resource_target(h) is not None
    }

    seen: Set[str] = set()
    pending = [name]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        used = _body_names(defs.get(current) or assigns[current])
        if used.attrs & _REMOTE_ATTRS:
            return True
        for ref in used.names:
            if ref in other_resources or ref in runpod_names or ref in local_imports:
                return True
            if ref in seen:
                continue
            if ref in defs or ref in assigns:
                pending.append(ref)
    return False
