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
from typing import Any, AsyncIterator, Optional

from .invoker import Invoker, StreamInvoker
from .job import Job
from .targets import SentinelTarget

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
        self._job_options: dict = {}
        self.remote = Invoker(self._remote_async)
        self.stream = StreamInvoker(self._stream_async)
        self.spawn = Invoker(self._spawn_async)
        self.job = Invoker(self._job_async)

    def with_options(
        self,
        *,
        webhook: Optional[str] = None,
        execution_timeout: Optional[int] = None,
        ttl: Optional[int] = None,
        low_priority: Optional[bool] = None,
        s3_config: Optional[dict] = None,
    ) -> "Queue":
        """bind per-job options for the next call, returning a new stub.

        options apply per invocation and stack across chained calls; the
        original stub is untouched.
        """
        from .targets import build_job_options, merge_job_options

        new_options = build_job_options(
            webhook, execution_timeout, ttl, low_priority, s3_config
        )
        clone = Queue(
            app=self.app_name,
            name=self.resource_name,
            id=self.resource_id,
            env=self.env,
        )
        clone._job_options = merge_job_options(self._job_options, new_options)
        return clone

    def _payload(self, kwargs: dict) -> dict:
        payload = {"input": kwargs or {"__empty": True}}
        if self._job_options:
            payload.update(self._job_options)
        return payload

    async def _remote_async(self, **kwargs: Any) -> Any:
        return await self._target().invoke(self._payload(kwargs))

    async def _spawn_async(self, **kwargs: Any) -> Job:
        target = self._target()
        data = await target.submit(self._payload(kwargs))
        return Job(data, target)

    async def _stream_async(self, **kwargs: Any) -> AsyncIterator[Any]:
        target = self._target()
        data = await target.submit(self._payload(kwargs))
        async for chunk in target.stream_job(data["id"]):
            yield chunk

    async def _job_async(self, job_id: str) -> Job:
        return Job(
            {"id": job_id, "status": "UNKNOWN"},
            self._target(),
        )

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
