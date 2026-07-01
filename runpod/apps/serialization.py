"""argument and result serialization for remote execution.

args/kwargs cross the wire as base64-encoded cloudpickle strings, the
format the worker runtime images expect. function source is extracted
with decorators stripped so the worker can exec it standalone.
"""

import ast
import base64
import inspect
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
    return cloudpickle.loads(base64.b64decode(result_b64))


def get_function_source(fn: Callable) -> str:
    """extract a function's source with decorators stripped.

    the worker execs this source into a fresh namespace and calls the
    function by name, so the emitted code must be a bare def.
    """
    fn = inspect.unwrap(fn)
    source = textwrap.dedent(inspect.getsource(fn))

    module = ast.parse(source)
    fn_def = None
    for node in ast.walk(module):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == fn.__name__
        ):
            fn_def = node
            break
    if fn_def is None:
        raise ValueError(f"could not find function definition for {fn.__name__}")

    # slice from the def line, dropping decorator lines above it
    lines = source.split("\n")
    fn_lines = lines[fn_def.lineno - 1 :]
    return textwrap.dedent("\n".join(fn_lines))
