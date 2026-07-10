"""shared FunctionRequest execution engine.

runs one function from shipped source: installs dependencies, execs the
module, resolves the function, and serializes the result per the
request's serialization_format. every runtime that executes live
requests (task pods, queue workers in dev mode, api servers) drives
this same engine.

stdlib-only (cloudpickle optional, required only for the cloudpickle
serialization format) so it can ship as part of a single-file bootstrap
onto any python image.
"""

import base64
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import traceback
from contextlib import redirect_stdout


def _load_cloudpickle(install: bool = False):
    try:
        import cloudpickle

        return cloudpickle
    except ImportError:
        if not install:
            return None
    # bare images (custom image= without the runtime baked in) may lack
    # cloudpickle; install it on demand the same way dependencies are
    if _install(["cloudpickle"], "cloudpickle"):
        return None
    import cloudpickle

    return cloudpickle


def _install(packages, label):
    if not packages:
        return None
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", *packages],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return f"failed to install {label}: {result.stderr[-2000:]}"
    return None


_apt_updated = False


def _install_system(packages):
    """install apt packages; requires a debian-family image with root."""
    global _apt_updated
    if not packages:
        return None
    if shutil.which("apt-get") is None:
        return (
            f"system dependencies {packages} requested but apt-get is not "
            f"available in this image; use a debian-based image or bake "
            f"them in"
        )
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    if not _apt_updated:
        update = subprocess.run(
            ["apt-get", "update", "-qq"], capture_output=True, text=True, env=env
        )
        if update.returncode != 0:
            return f"apt-get update failed: {update.stderr[-2000:]}"
        _apt_updated = True
    result = subprocess.run(
        ["apt-get", "install", "-y", "-qq", "--no-install-recommends", *packages],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        return (
            f"failed to install system dependencies {packages}: "
            f"{result.stderr[-2000:]}"
        )
    return None


def _deserialize_args(request):
    fmt = request.get("serialization_format", "cloudpickle")
    args = request.get("args") or []
    kwargs = request.get("kwargs") or {}
    if fmt == "json":
        return list(args), dict(kwargs)
    cloudpickle = _load_cloudpickle(install=True)
    if cloudpickle is None:
        raise RuntimeError("cloudpickle not available for argument deserialization")
    args = [cloudpickle.loads(base64.b64decode(a)) for a in args]
    kwargs = {k: cloudpickle.loads(base64.b64decode(v)) for k, v in kwargs.items()}
    return args, kwargs


def _serialize_result(result, fmt):
    if fmt == "json":
        # json-format requests promise json on the wire; failing here
        # keeps dev behavior identical to a deployed endpoint
        try:
            json.dumps(result)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                f"return value must be json-serializable "
                f"(this request uses the json wire format): {exc}"
            ) from exc
        return {"json_result": result}
    cloudpickle = _load_cloudpickle(install=True)
    if cloudpickle is None:
        return {"json_result": result}
    return {"result": base64.b64encode(cloudpickle.dumps(result)).decode("utf-8")}


def resolve_request(request):
    """prepare one FunctionRequest for execution.

    installs dependencies, execs the shipped source, and resolves the
    target function. returns ((fn, args, kwargs), None) on success or
    (None, response_dict) on failure.
    """
    error = _install_system(request.get("system_dependencies"))
    if error:
        return None, {"success": False, "error": error}
    error = _install(request.get("dependencies"), "dependencies")
    if error:
        return None, {"success": False, "error": error}

    function_name = request.get("function_name")
    function_code = request.get("function_code")
    if not function_name or not function_code:
        return None, {
            "success": False,
            "error": "function_name and function_code are required",
        }

    # exec mirrors deployed-mode module import: the code is the
    # user's full module, so __name__ is set like an import would
    # (main guards stay inert) and decorated handles resolve to
    # their wrapped functions. the source is written to a real
    # file first so inspect.getsource works inside the function
    # (nested .remote() calls re-extract sibling source)
    source_path = _materialize_source(function_code)
    namespace = {"__name__": "__runpod_live__", "__file__": source_path}
    code_obj = compile(function_code, source_path, "exec")
    exec(code_obj, namespace)  # noqa: S102 - that is the job
    if function_name not in namespace:
        return None, {
            "success": False,
            "error": f"function '{function_name}' not found in provided code",
        }
    fn = namespace[function_name]
    fn = getattr(fn, "_fn", fn)

    args, kwargs = _deserialize_args(request)
    return (fn, args, kwargs), None


def serialize_chunk(chunk, request):
    """wrap one generator chunk in the response envelope.

    the __stream__ marker lets clients tell a stream of chunks apart
    from a single aggregated function response.
    """
    response = {"success": True, "__stream__": True}
    response.update(
        _serialize_result(
            chunk, request.get("serialization_format", "cloudpickle")
        )
    )
    return response


def execute_request(request):
    """run one FunctionRequest to completion, returning a response dict."""
    stdout_io = io.StringIO()
    try:
        prepared, error_response = resolve_request(request)
        if error_response is not None:
            return error_response
        fn, args, kwargs = prepared

        # stdout is teed: captured for the job response and written
        # through to the real stdout so container logs (and live log
        # streaming in dev sessions) carry the function's prints.
        # sys.stderr carries the runner's own request logs from other
        # threads, which must not leak into job output
        with redirect_stdout(_Tee(stdout_io, sys.__stdout__)):
            result = fn(*args, **kwargs)
            if hasattr(result, "__await__"):
                result = _run_awaitable(result)
            # generators aggregate so live .remote() matches the
            # deployed worker's return_aggregate_stream output
            elif inspect.isgenerator(result):
                result = list(result)
            elif inspect.isasyncgen(result):
                result = _run_awaitable(_drain_async_gen(result))

        response = {"success": True, "stdout": stdout_io.getvalue()}
        response.update(
            _serialize_result(
                result, request.get("serialization_format", "cloudpickle")
            )
        )
        return response
    except Exception:  # noqa: BLE001 - all failures go over the wire
        return {
            "success": False,
            "error": traceback.format_exc(),
            "stdout": stdout_io.getvalue(),
        }


def _materialize_source(function_code):
    """persist request source to a stable path for inspect/linecache."""
    import hashlib
    import tempfile

    digest = hashlib.sha256(function_code.encode()).hexdigest()[:16]
    path = os.path.join(
        tempfile.gettempdir(), f"runpod_live_{digest}.py"
    )
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(function_code)
    return path


class _Tee(io.TextIOBase):
    """write-through to multiple streams, flushing eagerly so log
    followers see lines as they happen."""

    def __init__(self, *streams):
        self._streams = [s for s in streams if s is not None]

    def write(self, s):
        for stream in self._streams:
            try:
                stream.write(s)
                stream.flush()
            except (ValueError, OSError):
                # one sink failing (closed pipe) must not lose the
                # write on the other sinks
                pass
        return len(s)

    def flush(self):
        for stream in self._streams:
            try:
                stream.flush()
            except (ValueError, OSError):
                # closed sinks are skipped, same as write
                pass


def _run_awaitable(awaitable):
    """drive an awaitable to completion from sync code.

    task pods run this on plain threads, but queue workers in live mode
    call execute_request from inside the serverless event loop, where
    asyncio.run would raise; a private loop on a helper thread covers
    both.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await(awaitable))

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _await(awaitable)).result()


async def _drain_async_gen(agen):
    return [chunk async for chunk in agen]


async def _await(awaitable):
    return await awaitable
