"""generic queue worker for app endpoints.

two serving modes, chosen at startup:

deployed mode (rp deploy):
    the build artifact is unpacked at RUNPOD_APP_DIR and
    FLASH_RESOURCE_NAME identifies this resource. the worker imports
    the user's module, resolves the decorated function from the
    manifest, runs its @init hook, and serves jobs by calling the
    function body directly. job input is plain kwargs; source never
    travels with requests.

live mode (rp dev):
    no artifact. each job carries a FunctionRequest (source +
    serialized args) which is executed via the shared engine in
    runpod.runtimes.executor.
"""

import importlib
import inspect
import json
import os
import sys

import runpod

APP_DIR = os.environ.get("RUNPOD_APP_DIR", "/app")
MANIFEST_NAME = "runpod_manifest.json"


def _resource_name() -> str:
    return os.environ.get("FLASH_RESOURCE_NAME") or os.environ.get(
        "RUNPOD_RESOURCE_NAME", ""
    )


def _is_deployed() -> bool:
    return bool(_resource_name()) and os.path.isfile(
        os.path.join(APP_DIR, MANIFEST_NAME)
    )


def _load_deployed_handle():
    """import the user's module and return the FunctionHandle for this
    resource."""
    with open(os.path.join(APP_DIR, MANIFEST_NAME)) as f:
        manifest = json.load(f)

    name = _resource_name()
    entry = next(
        (r for r in manifest.get("resources", []) if r.get("name") == name),
        None,
    )
    if entry is None:
        raise RuntimeError(
            f"resource '{name}' not in manifest "
            f"(has: {[r.get('name') for r in manifest.get('resources', [])]})"
        )

    if APP_DIR not in sys.path:
        sys.path.insert(0, APP_DIR)
    module = importlib.import_module(entry["module"])

    from runpod.apps.handles import FunctionHandle

    for attr in vars(module).values():
        if (
            isinstance(attr, FunctionHandle)
            and attr.spec.name == name
        ):
            return attr
    raise RuntimeError(
        f"no @app.queue/@app.task handle named '{name}' found in "
        f"module '{entry['module']}'"
    )


def _job_kwargs(job: dict) -> dict:
    body = dict(job.get("input") or {})
    body.pop("__empty", None)
    return body


def _make_deployed_handler(handle):
    """serverless handler that calls the user function directly.

    generator functions become generator handlers so the serverless
    core streams partial outputs to /stream as they are yielded.
    """
    fn = handle._fn

    if inspect.isasyncgenfunction(fn):

        async def async_gen_handler(job: dict):
            async for chunk in fn(**_job_kwargs(job)):
                yield chunk

        return async_gen_handler

    if inspect.isgeneratorfunction(fn):

        def gen_handler(job: dict):
            yield from fn(**_job_kwargs(job))

        return gen_handler

    async def handler(job: dict) -> dict:
        result = fn(**_job_kwargs(job))
        if inspect.isawaitable(result):
            result = await result
        return result

    return handler


def _run_init(handle) -> None:
    """run the resource's @init hook before serving."""
    init_fn = getattr(handle, "_init_fn", None)
    if init_fn is None:
        return
    result = init_fn()
    if inspect.isawaitable(result):
        import asyncio

        asyncio.run(result)


async def _live_handler(job: dict):
    """execute a FunctionRequest, streaming when the function is a
    generator so dev sessions behave exactly like deployed workers."""
    from runpod.runtimes.executor import (
        execute_request,
        resolve_request,
        serialize_chunk,
    )

    request = job.get("input") or {}
    prepared, error_response = resolve_request(request)
    if error_response is not None:
        yield error_response
        return
    fn, args, kwargs = prepared

    if inspect.isasyncgenfunction(fn):
        async for chunk in fn(*args, **kwargs):
            yield serialize_chunk(chunk, request)
        return
    if inspect.isgeneratorfunction(fn):
        for chunk in fn(*args, **kwargs):
            yield serialize_chunk(chunk, request)
        return

    yield execute_request(request)


def _max_concurrency() -> int:
    """jobs one worker may run at once (RUNPOD_MAX_CONCURRENCY, min 1)."""
    try:
        return max(1, int(os.environ.get("RUNPOD_MAX_CONCURRENCY", "1")))
    except ValueError:
        return 1


def _worker_config(handler) -> dict:
    config: dict = {"handler": handler}
    from runpod.serverless.modules.rp_handler import is_generator

    if is_generator(handler):
        # generator jobs stream partials and still finish with a full
        # output, so .remote()/result() work alongside .stream()
        config["return_aggregate_stream"] = True
    concurrency = _max_concurrency()
    if concurrency > 1:
        config["concurrency_modifier"] = lambda _current: concurrency
    return config


def main() -> None:
    if _is_deployed():
        handle = _load_deployed_handle()
        _run_init(handle)
        runpod.serverless.start(
            _worker_config(_make_deployed_handler(handle))
        )
    else:
        # the live handler is a generator so it can stream; aggregation
        # keeps .remote() working for plain functions and generators alike
        runpod.serverless.start(_worker_config(_live_handler))


if __name__ == "__main__":
    main()
