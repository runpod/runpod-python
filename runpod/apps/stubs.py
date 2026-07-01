"""stubs for resources deployed from other codebases.

when you know a resource exists but don't have its source, a stub gives
you the same invocation surface as a local handle:

    external_queue = runpod.Queue(app="other-app", name="transcribe")
    external_queue.remote(audio_url="https://...")

    external_api = runpod.Api(app="other-app", name="inference")
    external_api.post("/generate", {"prompt": "hi"})

resolution goes through the same server-side sentinel path as deployed
handles, so a stub is just a handle without a function body.
"""

import os
from typing import Any, Dict, Optional

from .errors import EndpointNotFound, RemoteExecutionError
from .invoker import Invoker
from .targets import DEFAULT_TIMEOUT_SECONDS, SentinelTarget

DEFAULT_ENV = "default"


class _StubBase:
    def __init__(
        self,
        *,
        app: str,
        name: Optional[str] = None,
        id: Optional[str] = None,
        env: Optional[str] = None,
    ):
        if (name is None) == (id is None):
            raise ValueError("provide exactly one of name= or id=")
        self.app_name = app
        self.resource_name = name
        self.resource_id = id
        self.env = env or os.getenv("FLASH_ENVIRONMENT") or DEFAULT_ENV

    def _target(self) -> SentinelTarget:
        # id-based stubs still route through the sentinel; the id is
        # passed as the resource key and resolved server-side
        resource = self.resource_name or self.resource_id or ""
        return SentinelTarget(self.app_name, self.env, resource)


class Queue(_StubBase):
    """client for a queue resource deployed elsewhere."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.remote = Invoker(self._remote_async)
        self.spawn = Invoker(self._spawn_async)

    async def _remote_async(self, **kwargs: Any) -> Any:
        payload = {"input": kwargs or {"__empty": True}}
        return await self._target().invoke(payload)

    async def _spawn_async(self, **kwargs: Any) -> Dict[str, Any]:
        payload = {"input": kwargs or {"__empty": True}}
        return await self._target().submit(payload)

    def __repr__(self) -> str:
        ref = self.resource_name or self.resource_id
        return f"<Queue stub {self.app_name}/{ref}>"


class _StubRouteCaller:
    __slots__ = ("_stub", "_method")

    def __init__(self, stub: "Api", method: str):
        self._stub = stub
        self._method = method

    def __call__(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        from .context import block

        return block(self.aio(path, body, **kwargs))

    async def aio(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        return await self._stub._target().request(
            self._method, path, body, **kwargs
        )


class Api(_StubBase):
    """client for an api resource deployed elsewhere."""

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.get = _StubRouteCaller(self, "GET")
        self.post = _StubRouteCaller(self, "POST")
        self.put = _StubRouteCaller(self, "PUT")
        self.delete = _StubRouteCaller(self, "DELETE")
        self.patch = _StubRouteCaller(self, "PATCH")

    def __repr__(self) -> str:
        ref = self.resource_name or self.resource_id
        return f"<Api stub {self.app_name}/{ref}>"
