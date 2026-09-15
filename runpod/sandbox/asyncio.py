"""Asynchronous sandbox handles, lifecycle management, and typed log streams."""

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from types import TracebackType
from typing import Any, Optional, TypeVar

from runpod.api.sandboxes import AsyncSandboxAPI
from runpod.error import QueryError
from runpod.sandbox.models import (
    ExecResult,
    LogEvent,
    LogSource,
    SandboxCompute,
    SandboxExecutionError,
    SandboxInfo,
    SandboxStartupTimeout,
    SandboxState,
    SandboxStateError,
)

_T = TypeVar("_T")


def _timeout(value: float, name: str, *, allow_zero: bool = False) -> float:
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
        raise ValueError(
            f"{name} must be finite and {'nonnegative' if allow_zero else 'positive'}"
        )
    return value


async def _cleanup(
    operation: Awaitable[None],
    timeout: float,
    original: Optional[BaseException] = None,
) -> None:
    """finish bounded cleanup; callers propagate an existing error on success."""
    task = asyncio.create_task(asyncio.wait_for(operation, timeout))
    interrupted = original
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if interrupted is None:
                interrupted = error
        except BaseException:
            # task.result() below propagates the failure with its cleanup context.
            break
    try:
        task.result()
    except BaseException as error:
        if interrupted is not None:
            raise interrupted from error
        raise
    if interrupted is not None and original is None:
        raise interrupted


class AsyncioSandbox:
    """A cached sandbox handle; construction never performs network I/O.

    Supply exactly one of ``image_name`` or ``template_id``. Unspecified
    resource and lifetime options are left to the server's account policy.
    Created handles own their remote sandbox in a context; ``get`` and ``list``
    return borrowed handles whose context only closes local resources.
    """

    def __init__(
        self,
        *,
        image_name: Optional[str] = None,
        template_id: Optional[str] = None,
        name: Optional[str] = None,
        cpu_flavor_id: Optional[str] = None,
        vcpu_count: Optional[int] = None,
        memory_in_gb: Optional[int] = None,
        data_center_id: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        idle_timeout_seconds: Optional[int] = None,
        max_lifetime_seconds: Optional[int] = None,
        labels: Optional[Mapping[str, str]] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        request_timeout: float = 30,
        startup_timeout: float = 60,
    ) -> None:
        if (image_name is None) == (template_id is None):
            raise ValueError("Supply exactly one of image_name or template_id")
        self._initialize(api_key, base_url, request_timeout, startup_timeout)
        self._create_body = {
            "imageName": image_name,
            "templateId": template_id,
            "name": name,
            "cpuFlavorId": cpu_flavor_id,
            "vcpuCount": vcpu_count,
            "memoryInGb": memory_in_gb,
            "dataCenterId": data_center_id,
            "env": dict(env) if env is not None else None,
            "idleTimeoutSeconds": idle_timeout_seconds,
            "maxLifetimeSeconds": max_lifetime_seconds,
            "labels": dict(labels) if labels is not None else None,
        }

    def _initialize(
        self,
        api_key: Optional[str],
        base_url: Optional[str],
        request_timeout: float,
        startup_timeout: float,
    ) -> None:
        self._request_timeout = _timeout(request_timeout, "request_timeout")
        self._startup_timeout = _timeout(
            startup_timeout, "startup_timeout", allow_zero=True
        )
        self._api = AsyncSandboxAPI(api_key, base_url, request_timeout)
        self._info: Optional[SandboxInfo] = None
        self._sandbox_id: Optional[str] = None
        self._create_body: Optional[dict[str, Any]] = None
        self._owned = False
        self._entering = False
        self._creating = False
        self._streams: set[AsyncSandboxLogStream] = set()

    @classmethod
    async def create(cls, **options: Any) -> "AsyncioSandbox":
        """Create and return an owned handle, without claiming container readiness."""
        sandbox = cls(**options)
        await sandbox._create()
        return sandbox

    @classmethod
    async def get(
        cls,
        sandbox_id: str,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        request_timeout: float = 30,
        startup_timeout: float = 60,
    ) -> "AsyncioSandbox":
        """Fetch a borrowed handle; leaving its context does not terminate it."""
        sandbox = cls.__new__(cls)
        sandbox._initialize(api_key, base_url, request_timeout, startup_timeout)
        sandbox._sandbox_id = sandbox_id
        try:
            await sandbox.refresh()
        except BaseException as error:
            await _cleanup(sandbox.close(), request_timeout, error)
            raise
        return sandbox

    @classmethod
    async def list(
        cls,
        *,
        state: Optional[SandboxState] = None,
        labels: Optional[Mapping[str, str]] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        request_timeout: float = 30,
        startup_timeout: float = 60,
    ) -> list["AsyncioSandbox"]:
        """Return borrowed snapshots with independent, not-yet-open sessions."""
        _timeout(request_timeout, "request_timeout")
        _timeout(startup_timeout, "startup_timeout", allow_zero=True)
        api = AsyncSandboxAPI(api_key, base_url, request_timeout)
        try:
            rows = await api.list(state=state, labels=labels)
            snapshots = [SandboxInfo.from_dict(row) for row in rows]
        except BaseException as error:
            await _cleanup(api.close(), request_timeout, error)
            raise
        else:
            await _cleanup(api.close(), request_timeout)
        handles = []
        for info in snapshots:
            sandbox = cls.__new__(cls)
            sandbox._initialize(api_key, base_url, request_timeout, startup_timeout)
            sandbox._info = info
            sandbox._sandbox_id = info.id
            handles.append(sandbox)
        return handles

    @property
    def info(self) -> SandboxInfo:
        """The latest server snapshot; raises before creation, never fetches."""
        if self._info is None:
            raise RuntimeError("Sandbox has not been created or fetched")
        return self._info

    @property
    def id(self) -> str:
        """The cached sandbox identifier."""
        return self.info.id

    @property
    def state(self) -> SandboxState:
        """The cached lifecycle state, not a container-readiness guarantee."""
        return self.info.state

    @property
    def compute(self) -> Optional[SandboxCompute]:
        """The cached allocation, if one currently exists."""
        return self.info.compute

    @property
    def expires_at(self) -> datetime:
        """The cached maximum-lifetime deadline."""
        return self.info.expires_at

    async def _create(self) -> None:
        if self._creating:
            raise RuntimeError("Sandbox creation is already in progress")
        if self._sandbox_id is not None:
            return
        if self._create_body is None:
            raise RuntimeError("A borrowed sandbox cannot create a new resource")
        self._creating = True

        async def create_remote() -> None:
            data = await self._api.create(self._create_body)
            sandbox_id = data["id"]
            if not isinstance(sandbox_id, str) or not sandbox_id:
                raise ValueError("Create response has no valid sandbox id")
            self._sandbox_id = sandbox_id
            self._owned = True
            self._info = SandboxInfo.from_dict(data)

        task = asyncio.create_task(create_remote())
        try:
            await asyncio.shield(task)
        except BaseException as original:

            async def recover_and_release() -> None:
                recovery_error = None
                try:
                    if not task.done():
                        await asyncio.wait_for(task, self._request_timeout)
                    else:
                        task.result()
                except BaseException as error:
                    # finish releasing the resource before propagating recovery errors.
                    if error is not original:
                        recovery_error = error
                try:
                    if self._sandbox_id is not None:
                        await self.terminate()
                    else:
                        await self.close()
                except BaseException as error:
                    if recovery_error is not None:
                        raise error from recovery_error
                    raise
                if recovery_error is not None:
                    raise recovery_error

            await _cleanup(recover_and_release(), 2 * self._request_timeout, original)
            raise
        finally:
            self._creating = False

    async def __aenter__(self) -> "AsyncioSandbox":
        """Create if needed and enter a single, non-reentrant ownership scope."""
        if self._entering:
            raise RuntimeError(
                "Sandbox contexts cannot be nested or entered concurrently"
            )
        self._entering = True
        try:
            await self._create()
            return self
        except BaseException:
            self._entering = False
            raise

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        """Release the owned resource, shielding bounded cleanup from cancellation."""
        try:
            operation = self.terminate() if self._owned else self.close()
            await _cleanup(operation, self._request_timeout, exc)
        finally:
            self._entering = False

    async def refresh(self) -> SandboxInfo:
        """Fetch a fresh snapshot, including after local close or termination."""
        if self._sandbox_id is None:
            raise RuntimeError("Sandbox has not been created or fetched")
        self._info = SandboxInfo.from_dict(await self._api.get(self._sandbox_id))
        return self._info

    async def _ready_operation(
        self,
        operation: Callable[[], Awaitable[_T]],
        startup_timeout: Optional[float],
        *,
        handshake: bool = False,
    ) -> _T:
        timeout = (
            self._startup_timeout
            if startup_timeout is None
            else _timeout(startup_timeout, "startup_timeout", allow_zero=True)
        )
        sandbox_id = self.id
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            try:
                if handshake and timeout > 0:
                    try:
                        return await asyncio.wait_for(
                            operation(), deadline - loop.time()
                        )
                    except asyncio.TimeoutError as error:
                        if loop.time() >= deadline:
                            raise SandboxStartupTimeout(sandbox_id, timeout) from error
                        raise
                return await operation()
            except QueryError as error:
                if error.status_code != 409 or timeout == 0:
                    raise
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise SandboxStartupTimeout(sandbox_id, timeout) from error
                try:
                    info = await asyncio.wait_for(self.refresh(), remaining)
                except asyncio.TimeoutError as refresh_error:
                    if loop.time() >= deadline:
                        raise SandboxStartupTimeout(
                            sandbox_id, timeout
                        ) from refresh_error
                    raise
                if info.state in ("FAILED", "TERMINATED"):
                    raise SandboxStateError(info) from error
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise SandboxStartupTimeout(sandbox_id, timeout) from error
                await asyncio.sleep(min(0.25, remaining))
                if loop.time() >= deadline:
                    raise SandboxStartupTimeout(sandbox_id, timeout) from error

    async def exec(
        self,
        command: Sequence[str],
        *,
        check: bool = False,
        startup_timeout: Optional[float] = None,
    ) -> ExecResult:
        """Execute argv, retrying only explicit 409 startup rejections.

        The startup deadline limits retries, not execution of an accepted
        command. Each request retains its request timeout. Transport errors,
        timeouts, and other HTTP errors are never retried because the command
        might already have executed. ``check=True`` preserves output on failure.
        """
        if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
            raise TypeError(
                "command must be a nonempty sequence of strings, not a string"
            )
        if not command or any(not isinstance(argument, str) for argument in command):
            raise ValueError("command must be a nonempty sequence of strings")
        argv = tuple(command)
        data = await self._ready_operation(
            lambda: self._api.exec(self.id, argv), startup_timeout
        )
        result = ExecResult(output=data["output"], error=data.get("error"))
        if check and result.error is not None:
            raise SandboxExecutionError(self.id, result)
        return result

    def logs(
        self,
        *,
        source: Optional[LogSource] = None,
        tail: Optional[int] = None,
        since: Optional[str] = None,
        last_event_id: Optional[str] = None,
        startup_timeout: Optional[float] = None,
    ) -> "AsyncSandboxLogStream":
        """Return a lazy, closeable stream of main-process or lifecycle logs.

        This does not include exec output. Only the initial HTTP handshake is
        retried on 409; disconnects and errors after opening are never replayed.
        Use an async context or ``aclose`` when stopping iteration early.
        """
        sandbox_id = self.id
        stream = AsyncSandboxLogStream(
            self, sandbox_id, source, tail, since, last_event_id, startup_timeout
        )
        self._streams.add(stream)
        return stream

    async def terminate(self) -> None:
        """Delete remotely and always close locally, even if deletion fails.

        The readable remote record remains. Only successful deletion changes
        the cached state; timestamps are left untouched until ``refresh``.
        """
        try:
            if self._sandbox_id is None:
                raise RuntimeError("Sandbox has not been created or fetched")
            await self._api.terminate(self._sandbox_id)
            if self._info is not None:
                self._info = replace(self._info, state="TERMINATED", compute=None)
        except BaseException as error:
            await _cleanup(self.close(), self._request_timeout, error)
            raise
        else:
            await _cleanup(self.close(), self._request_timeout)

    async def close(self) -> None:
        """Close streams and the local session without deleting; later reuse is allowed."""
        error = None
        for stream in tuple(self._streams):
            try:
                await stream.aclose()
            except BaseException as stream_error:
                # close every stream before propagating cancellation or another failure.
                if error is None:
                    error = stream_error
        try:
            await self._api.close()
        except BaseException as close_error:
            if error is not None:
                raise error from close_error
            raise
        if error is not None:
            raise error


class AsyncSandboxLogStream:
    """A handle-owned typed log stream with a separately awaitable handshake."""

    def __init__(
        self,
        sandbox: AsyncioSandbox,
        sandbox_id: str,
        source: Optional[LogSource],
        tail: Optional[int],
        since: Optional[str],
        last_event_id: Optional[str],
        startup_timeout: Optional[float],
    ) -> None:
        self._sandbox = sandbox
        self._sandbox_id = sandbox_id
        self._options = dict(
            source=source, tail=tail, since=since, last_event_id=last_event_id
        )
        self._startup_timeout = startup_timeout
        self._stream: Any = None
        self._opened = False
        self._closed = False
        self._opening: Optional[asyncio.Task[None]] = None

    async def _open_once(self) -> None:
        stream = self._sandbox._api.logs(self._sandbox_id, **self._options)
        self._stream = stream
        try:
            await stream.open()
        except BaseException as error:
            self._stream = None
            await _cleanup(stream.aclose(), self._sandbox._request_timeout, error)
            raise

    async def open(self) -> "AsyncSandboxLogStream":
        """Complete the HTTP handshake without waiting for the first log event."""
        if self._closed:
            raise RuntimeError("Log stream is closed")
        if not self._opened:
            if self._opening is None:
                self._opening = asyncio.create_task(
                    self._sandbox._ready_operation(
                        self._open_once, self._startup_timeout, handshake=True
                    )
                )
            try:
                await self._opening
                if self._closed:
                    raise RuntimeError("Log stream was closed while opening")
                self._opened = True
            except BaseException as error:
                await _cleanup(self.aclose(), self._sandbox._request_timeout, error)
                raise
        return self

    def __aiter__(self) -> "AsyncSandboxLogStream":
        return self

    async def __anext__(self) -> LogEvent:
        if self._closed:
            raise StopAsyncIteration
        try:
            await self.open()
            return LogEvent.from_dict(await self._stream.__anext__())
        except StopAsyncIteration:
            await self.aclose()
            raise
        except BaseException as error:
            await _cleanup(self.aclose(), self._sandbox._request_timeout, error)
            raise

    async def aclose(self) -> None:
        """Release the response and detach from the sandbox; idempotent."""
        if self._closed:
            return
        self._closed = True
        self._sandbox._streams.discard(self)
        try:
            if self._opening is not None and not self._opening.done():
                self._opening.cancel()
                await asyncio.gather(self._opening, return_exceptions=True)
        finally:
            stream, self._stream = self._stream, None
            if stream is not None:
                await stream.aclose()

    async def __aenter__(self) -> "AsyncSandboxLogStream":
        return await self.open()

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        await _cleanup(self.aclose(), self._sandbox._request_timeout, exc)
