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
    no artifact. the client pushes the user's module source to
    /_runpod/sync (once per source change); the matching api class is
    materialized from it and serves the real routes, so dev requests
    hit the same handlers a deployed worker would. /execute remains
    for FunctionRequest payloads.
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

    _mount_routes(app, handle, instance)
    return app


def _mount_routes(app: Any, handle, instance) -> None:
    """add each @get/@post route of an ApiHandle's class to a fastapi app."""
    from fastapi import Request

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


def _build_factory_app(handle) -> Any:
    """call the user's asgi factory and ensure /ping exists.

    fastapi/starlette apps get a /ping route added when missing; other
    asgi callables are wrapped so /ping is answered before delegating.
    """
    app = handle._asgi_factory()

    routes = getattr(app, "routes", None)
    if routes is not None and hasattr(app, "get"):
        if not any(getattr(r, "path", None) == "/ping" for r in routes):

            @app.get("/ping")
            async def ping():
                return {"status": "healthy"}

        return app

    async def with_ping(scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/ping":
            body = b'{"status": "healthy"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await app(scope, receive, send)

    return with_ping


def _install_live_dependencies(request: dict) -> None:
    """install a live resource's dependencies before its module runs.

    dev workers boot on the bare runtime image; deployed workers get
    dependencies baked into the build artifact, so live mode installs
    them at sync time to match.
    """
    from runpod.runtimes.executor import _install, _install_system

    error = _install_system(request.get("system_dependencies"))
    if error:
        raise RuntimeError(error)
    error = _install(request.get("dependencies"), "dependencies")
    if error:
        raise RuntimeError(error)


async def _materialize_live_api(source: str, resource: str) -> Any:
    """exec shipped module source and build the api app for a resource.

    @init runs here, before the app is swapped in, so the first
    routed request already sees initialized state.
    """
    from fastapi import FastAPI

    from runpod.apps.handles import ApiHandle
    from runpod.runtimes.executor import _materialize_source

    path = _materialize_source(source)
    namespace: dict = {"__name__": "__runpod_live__", "__file__": path}
    exec(compile(source, path, "exec"), namespace)  # noqa: S102

    handle = None
    for value in namespace.values():
        if isinstance(value, ApiHandle) and value.spec.name == resource:
            handle = value
            break
    if handle is None:
        raise RuntimeError(
            f"no @app.api resource named '{resource}' in shipped module"
        )
    if handle._cls is None:
        return _build_factory_app(handle)

    instance = handle._cls()
    if handle._init_name:
        await _maybe_await(getattr(instance, handle._init_name)())
    app = FastAPI(title=f"{resource} (live)")
    _mount_routes(app, handle, instance)
    return app


class _LiveDispatcher:
    """asgi front for dev sessions.

    the client pushes module source to /_runpod/sync; the api app is
    materialized from it (rebuilt when the source hash changes) and
    serves every route. /ping and /execute always work.
    """

    def __init__(self):
        self._core = self._build_core()
        self._inner: Any = None
        self._hash: Optional[str] = None

    def _build_core(self) -> Any:
        import hashlib

        from fastapi import FastAPI

        app = FastAPI(title="runpod-live-api")

        @app.get("/ping")
        async def ping():
            return {"status": "healthy"}

        @app.post("/execute")
        async def execute(request: dict):
            from runpod.runtimes.executor import execute_request

            return execute_request(request.get("input", request))

        @app.post("/_runpod/sync")
        async def sync(request: dict):
            source = request.get("source") or ""
            resource = request.get("resource") or ""
            digest = hashlib.sha256(source.encode()).hexdigest()
            if digest != self._hash:
                _install_live_dependencies(request)
                self._inner = await _materialize_live_api(source, resource)
                self._hash = digest
            return {"status": "synced", "hash": digest}

        return app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self._core(scope, receive, send)
        path = scope.get("path", "")
        if path in ("/ping", "/execute", "/_runpod/sync") or self._inner is None:
            return await self._core(scope, receive, send)
        return await self._inner(scope, receive, send)


def _build_live_app() -> Any:
    return _LiveDispatcher()


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
