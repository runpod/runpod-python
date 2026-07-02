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
    serialized args) which is executed via the task runner's
    execute_request.
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


def _make_deployed_handler(handle):
    """serverless handler that calls the user function directly."""
    fn = handle._fn

    async def handler(job: dict) -> dict:
        body = dict(job.get("input") or {})
        body.pop("__empty", None)
        result = fn(**body)
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


def _live_handler(job: dict) -> dict:
    from runpod.runtimes.task.runner import execute_request

    return execute_request(job.get("input") or {})


def main() -> None:
    if _is_deployed():
        handle = _load_deployed_handle()
        _run_init(handle)
        runpod.serverless.start({"handler": _make_deployed_handler(handle)})
    else:
        runpod.serverless.start({"handler": _live_handler})


if __name__ == "__main__":
    main()
