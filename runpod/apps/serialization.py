"""json argument validation, response decoding, and function source shipping."""

import ast
import inspect
import os
import textwrap
from typing import Any, Callable

from .._serialization import validate_json
from .errors import RemoteExecutionError
from .protocol import FunctionResponse


def unwrap_function_response(output: dict) -> Any:
    try:
        response = FunctionResponse.from_output(output)
    except (TypeError, ValueError) as exc:
        raise RemoteExecutionError(f"invalid remote response: {exc}") from exc
    if not response.success:
        raise RemoteExecutionError(
            f"remote execution failed: {response.error or 'unknown'}"
        )
    return response.json_result


def require_json_args(fn: Callable, args: tuple, kwargs: dict) -> None:
    """validate queue and task arguments before remote execution."""
    validate_json(list(args), f"{fn.__name__}() arguments")
    validate_json(kwargs, f"{fn.__name__}() keyword arguments")


def get_function_source(fn: Callable) -> str:
    """the full source of the function's module.

    the worker execs this and resolves the function by name, exactly
    mirroring deployed mode (which imports the user's module from the
    build artifact): module-level imports, globals, and decorators
    behave identically in dev and deploy.
    """
    fn = inspect.unwrap(fn)
    module = inspect.getmodule(fn)
    if module is not None:
        try:
            return inspect.getsource(module)
        except (OSError, TypeError):
            pass
    # exec'd module (nested hop inside a live worker): the runner
    # materialized the shipped module to a real file; re-ship it whole
    # so decorators and globals keep working on the next worker
    filename = getattr(getattr(fn, "__code__", None), "co_filename", "")
    if filename and os.path.isfile(filename):
        with open(filename) as f:
            return f.read()
    # last resort (repl): the bare function body with decorators
    # stripped, since their context cannot travel
    return _bare_function_source(fn)


def _bare_function_source(fn: Callable) -> str:
    """a function's own def, decorators removed."""
    source = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == fn.__name__
        ):
            lines = source.split("\n")
            return textwrap.dedent("\n".join(lines[node.lineno - 1 :]))
    return source
