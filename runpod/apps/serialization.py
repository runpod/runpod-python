"""argument and result serialization for remote execution.

args/kwargs cross the wire as base64-encoded cloudpickle strings, the
format the worker runtime images expect. function source is extracted
with decorators stripped so the worker can exec it standalone.
"""

import ast
import base64
import inspect
import os
import textwrap
from typing import Any, Callable, Dict, List

import cloudpickle


def serialize_arg(arg: Any) -> str:
    return base64.b64encode(cloudpickle.dumps(arg)).decode("utf-8")


def serialize_args(args: tuple) -> List[str]:
    return [serialize_arg(a) for a in args]


def serialize_kwargs(kwargs: dict) -> Dict[str, str]:
    return {k: serialize_arg(v) for k, v in kwargs.items()}


def deserialize_result(result_b64: str) -> Any:
    try:
        return cloudpickle.loads(base64.b64decode(result_b64))
    except ModuleNotFoundError as exc:
        from .errors import RemoteExecutionError

        raise RemoteExecutionError(
            f"the remote result contains an object from the "
            f"'{exc.name}' package, which is not installed locally. "
            f"return plain python types (e.g. str(...) it) or install "
            f"'{exc.name}' on this machine."
        ) from exc


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
