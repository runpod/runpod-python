"""The README's Example section must stay valid code the SDK accepts.

The sample is the first thing new users copy, so these tests check that it
references only names it defines, that app discovery accepts it, and that a
synchronous entrypoint never spawns a task it cannot wait on.
"""

import ast
import builtins
import re
import symtable
from pathlib import Path

from runpod.apps.discovery import discover_apps

README = Path(__file__).resolve().parent.parent / "README.md"
EXAMPLE_HEADING = "## Example"
PYTHON_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)


def _example_source() -> str:
    text = README.read_text(encoding="utf-8")
    _, _, section = text.partition(EXAMPLE_HEADING)
    match = PYTHON_BLOCK.search(section)
    assert match, "README Example section has no python code block"
    return match.group(1)


def _module_bindings(table: symtable.SymbolTable) -> set:
    return {
        sym.get_name()
        for sym in table.get_symbols()
        if sym.is_assigned() or sym.is_imported()
    }


def _undefined_globals(source: str) -> set:
    top = symtable.symtable(source, "README example", "exec")
    known = _module_bindings(top) | set(dir(builtins))
    undefined = set()

    def visit(table: symtable.SymbolTable) -> None:
        for sym in table.get_symbols():
            if sym.is_referenced() and sym.is_global() and sym.get_name() not in known:
                undefined.add(sym.get_name())
        for child in table.get_children():
            visit(child)

    visit(top)
    return undefined


def _decorator_attr(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    return target.attr if isinstance(target, ast.Attribute) else ""


def test_example_references_only_defined_names():
    assert _undefined_globals(_example_source()) == set()


def test_example_is_accepted_by_app_discovery(tmp_path):
    sample = tmp_path / "main.py"
    sample.write_text(_example_source(), encoding="utf-8")

    apps = discover_apps(sample)

    assert len(apps) == 1


def test_sync_entrypoint_does_not_spawn_tasks():
    """A spawned task keeps its pod until awaited, which sync code cannot do."""
    tree = ast.parse(_example_source())
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    tasks = {
        fn.name
        for fn in functions
        if any(_decorator_attr(d) == "task" for d in fn.decorator_list)
    }
    entrypoints = [
        fn
        for fn in functions
        if any(_decorator_attr(d) == "local_entrypoint" for d in fn.decorator_list)
    ]

    spawned = {
        call.func.value.id
        for fn in entrypoints
        for call in ast.walk(fn)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "spawn"
        and isinstance(call.func.value, ast.Name)
    }

    assert tasks, "expected the example to define at least one task"
    assert spawned & tasks == set()
