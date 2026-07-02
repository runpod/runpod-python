"""generic api (load-balanced) server for app endpoints.

serves an asgi app on the port runpod's load balancer routes to
(PORT env, default 80), with /ping kept healthy for LB health checks.

two serving modes, chosen at startup:

deployed mode (rp deploy):
    the build artifact is unpacked at RUNPOD_APP_DIR and
    FLASH_RESOURCE_NAME identifies this resource. the server imports
    the user's module, finds the ApiHandle, and builds the asgi app:
      - class-based api: instantiate the class, run its @init method
        before /ping reports healthy, mount each @get/@post route
      - asgi factory: call the factory, serve what it returns

live mode (rp dev):
    no artifact. serves /execute, which runs FunctionRequest payloads
    (source per request) via the task runner's execute_request.
"""

import importlib
import inspect
import json
import logging
import os
import sys
from typing import Any, Optional

log = logging.getLogger("runpod.runtimes.api")

APP_DIR = os.environ.get("RUNPOD_APP_DIR", "/app")
MANIFEST_NAME = "runpod_manifest.json"
PORT = int(os.environ.get("PORT", "80"))


def _resource_name() -> str:
    return os.environ.get("FLASH_RESOURCE_NAME") or os.environ.get(
        "RUNPOD_RESOURCE_NAME", ""
    )


def _is_deployed() -> bool:
    return bool(_resource_name()) and os.path.isfile(
        os.path.join(APP_DIR, MANIFEST_NAME)
    )


def _load_api_handle():
    """import the user's module and return the ApiHandle for this resource."""
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

    from runpod.apps.handles import ApiHandle

    for attr in vars(module).values():
        if isinstance(attr, ApiHandle) and attr.spec.name == name:
            return attr
    raise RuntimeError(
        f"no @app.api handle named '{name}' found in module '{entry['module']}'"
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _build_class_app(handle) -> Any:
    """construct a fastapi app from an ApiHandle's decorated class.

    the class is instantiated once per worker; @init runs before /ping
    reports healthy so the LB only routes to ready workers.
    """
    from contextlib import asynccontextmanager

    from fastapi import FastAPI, Request

    cls = handle._cls
    instance = cls()
    ready = {"ok": False}

    @asynccontextmanager
    async def lifespan(_app):
        if handle._init_name:
            await _maybe_await(getattr(instance, handle._init_name)())
        ready["ok"] = True
        yield

    app = FastAPI(title=handle.spec.name, lifespan=lifespan)

    @app.get("/ping")
    async def ping():
        from fastapi.responses import JSONResponse

        if not ready["ok"]:
            return JSONResponse({"status": "initializing"}, status_code=204)
        return {"status": "healthy"}

    for route in handle.spec.routes:
        method = getattr(route, "method", None) or route["method"]
        path = getattr(route, "path", None) or route["path"]
        handler_name = (
            getattr(route, "handler_name", None) or route["handler"]
        )
        bound = getattr(instance, handler_name)

        def make_endpoint(fn):
            async def endpoint(request: Request):
                body = None
                if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                    try:
                        body = await request.json()
                    except Exception:  # noqa: BLE001 - empty/non-json body
                        body = None
                if body is not None:
                    return await _maybe_await(fn(body))
                return await _maybe_await(fn())

            return endpoint

        app.add_api_route(
            path, make_endpoint(bound), methods=[method], name=handler_name
        )

    return app


def _build_factory_app(handle) -> Any:
    """call the user's asgi factory and ensure /ping exists."""
    app = handle._asgi_factory()

    routes = getattr(app, "routes", [])
    if not any(getattr(r, "path", None) == "/ping" for r in routes):

        @app.get("/ping")
        async def ping():
            return {"status": "healthy"}

    return app


def _build_live_app() -> Any:
    """generic /execute server for dev sessions (source per request)."""
    from fastapi import FastAPI

    app = FastAPI(title="runpod-live-api")

    @app.get("/ping")
    async def ping():
        return {"status": "healthy"}

    @app.post("/execute")
    async def execute(request: dict):
        from runpod.runtimes.task.runner import execute_request

        return execute_request(request.get("input", request))

    return app


def build_app() -> Any:
    if _is_deployed():
        handle = _load_api_handle()
        if handle._cls is not None:
            return _build_class_app(handle)
        return _build_factory_app(handle)
    return _build_live_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        build_app(),
        host="0.0.0.0",
        port=PORT,
        timeout_keep_alive=600,
        log_level="info",
    )


if __name__ == "__main__":
    main()
