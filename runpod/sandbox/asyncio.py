"""Asynchronous sandbox handles, lifecycle management, and typed log streams."""

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from types import TracebackType
from typing import Any, Literal, Optional, TypeVar

from runpod.api.sandboxes import (
    AsyncCommandLogStream,
    AsyncSandboxAPI,
    SandboxConflictError,
    SandboxCreationError,
)
from runpod.apps.registry import resolve_registry_auth
from runpod.apps.volume import Volume, VolumeResolver, normalize_mounts, validate_mounts
from runpod.error import QueryError
from runpod.sandbox.models import (
    CommandInfo,
    CommandLogEnd,
    CommandLogLine,
    CommandOutput,
    CommandStream,
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


def _argv(command: Sequence[str]) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
        raise TypeError("command must be a nonempty sequence of strings, not a string")
    if not command or any(not isinstance(argument, str) for argument in command):
        raise ValueError("command must be a nonempty sequence of strings")
    return tuple(command)


def _cwd(cwd: Optional[str]) -> Optional[str]:
    if cwd is not None and (not isinstance(cwd, str) or not cwd.startswith("/")):
        raise ValueError("cwd must be an absolute path")
    return cwd


def _env(env: Optional[Mapping[str, str]]) -> Optional[dict[str, str]]:
    if env is None:
        return None
    if not isinstance(env, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in env.items()
    ):
        raise TypeError("env must map strings to strings")
    return dict(env)


def _int_in_range(value: Optional[int], name: str, low: int, high: int) -> None:
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not low <= value <= high
    ):
        raise ValueError(f"{name} must be an integer from {low} to {high}")


def _string_list(
    value: Optional[Sequence[str]], name: str, *, nonempty: bool = False
) -> Optional[list[str]]:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be a sequence of strings")
    if any(not isinstance(item, str) for item in value):
        raise TypeError(f"{name} must be a sequence of strings")
    if nonempty and (not value or any(not item for item in value)):
        raise ValueError(f"{name} must contain at least one nonempty string")
    return list(value)


def _ports(
    ports: Optional[Mapping[int, str | tuple[str, ...]]],
) -> Optional[list[dict[str, Any]]]:
    if ports is None:
        return None
    if not isinstance(ports, Mapping):
        raise TypeError("ports must map port numbers to protocols")
    rows = []
    for port, protocols in ports.items():
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            raise ValueError("port numbers must be integers from 1 to 65535")
        if isinstance(protocols, str):
            protocols = (protocols,)
        if not isinstance(protocols, tuple) or not protocols:
            raise TypeError("port protocols must be a string or nonempty tuple")
        transports = set()
        for protocol in protocols:
            if protocol not in ("http", "tcp", "udp"):
                raise ValueError("port protocols must be http, tcp, or udp")
            transport = "udp" if protocol == "udp" else "tcp"
            if transport in transports:
                raise ValueError(f"port {port} has overlapping protocols")
            transports.add(transport)
            rows.append({"port": port, "protocol": protocol})
    if len(rows) > 15:
        raise ValueError("sandboxes support at most 15 exposed ports")
    return rows


def _sandbox_mounts(
    mounts: Optional[Mapping[str, Volume]],
) -> Optional[dict[str, Volume]]:
    if mounts is None:
        return None
    normalized = normalize_mounts(mounts)
    validate_mounts(normalized, "sandbox", is_cpu=True)
    return normalized


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
    """a cached sandbox handle; construction never performs network I/O.

    supply at most one of ``image`` or ``template_id``; omitting both uses the
    server's default image. unspecified resource and lifetime options are left
    to the server's account policy. supply ``cpu_flavor_id`` and ``vcpu_count``
    together, or omit both for server defaults. memory is derived by the server
    from the CPU flavor and vCPU count.
    created handles own their remote sandbox in a context; ``get`` and ``list``
    return borrowed handles whose context only closes local resources.
    """

    def __init__(
        self,
        *,
        image: Optional[str] = None,
        template_id: Optional[str] = None,
        name: Optional[str] = None,
        cpu_flavor_id: Optional[str] = None,
        vcpu_count: Optional[int] = None,
        disk_gb: Optional[int] = None,
        data_center_ids: Optional[Sequence[str]] = None,
        mounts: Optional[Mapping[str, Volume]] = None,
        ports: Optional[
            Mapping[
                int,
                Literal["http", "tcp", "udp"]
                | tuple[Literal["http", "tcp", "udp"], ...],
            ]
        ] = None,
        cmd: Optional[Sequence[str]] = None,
        entrypoint: Optional[Sequence[str]] = None,
        start_ssh: Optional[bool] = None,
        registry_auth: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        idle_timeout_seconds: Optional[int] = None,
        max_lifetime_seconds: Optional[int] = None,
        labels: Optional[Mapping[str, str]] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        request_timeout: float = 30,
        startup_timeout: float = 60,
    ) -> None:
        if image is not None and template_id is not None:
            raise ValueError("Supply at most one of image or template_id")
        if (cpu_flavor_id is None) != (vcpu_count is None):
            raise ValueError("Supply cpu_flavor_id and vcpu_count together")
        self._initialize(api_key, base_url, request_timeout, startup_timeout)
        self._mounts = _sandbox_mounts(mounts)
        self._registry_auth = registry_auth
        self._create_body = {
            "image": image,
            "templateId": template_id,
            "name": name,
            "disk": disk_gb,
            "dataCenterIds": _string_list(
                data_center_ids, "data_center_ids", nonempty=True
            ),
            "ports": _ports(ports),
            "cmd": _string_list(cmd, "cmd"),
            "entrypoint": _string_list(entrypoint, "entrypoint"),
            "startSsh": start_ssh,
            "env": dict(env) if env is not None else None,
            "idleTimeoutSeconds": idle_timeout_seconds,
            "maxLifetimeSeconds": max_lifetime_seconds,
            "labels": dict(labels) if labels is not None else None,
        }
        if cpu_flavor_id is not None and vcpu_count is not None:
            self._create_body["cpu"] = {"id": cpu_flavor_id, "vcpuCount": vcpu_count}

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
        self._mounts: Optional[dict[str, Volume]] = None
        self._registry_auth: Optional[str] = None
        self._lifetime_lock = asyncio.Lock()
        self._owned = False
        self._entering = False
        self._creating = False
        self._streams: set[AsyncioSandboxLogStream] = set()

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
            body = dict(self._create_body)
            if self._mounts is not None:
                resolved = await VolumeResolver(api=self._api).resolve_mounts(
                    self._mounts
                )
                wire_mounts = {}
                ids = set()
                for mount in resolved:
                    if mount["id"] in ids:
                        raise ValueError("a sandbox cannot mount the same volume twice")
                    ids.add(mount["id"])
                    wire_mounts[mount["kind"]] = [
                        {"volumeId": mount["id"], "path": mount["path"]}
                    ]
                    if mount["kind"] == "network":
                        dc = mount["dataCenterId"]
                        requested = body.get("dataCenterIds")
                        if requested is not None and dc not in requested:
                            raise ValueError(
                                f"network volume data center {dc!r} is not in data_center_ids"
                            )
                        body["dataCenterIds"] = [dc]
                body["mounts"] = wire_mounts
            if self._registry_auth is not None:
                body["registry"] = await resolve_registry_auth(
                    self._registry_auth, api=self._api
                )
            try:
                data = await self._api.create(body)
            except SandboxCreationError as error:
                self._sandbox_id = error.sandbox_id
                self._owned = True
                raise
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

            async def recover_and_release(original_error: BaseException) -> None:
                recovery_error = None
                try:
                    if not task.done():
                        await asyncio.wait_for(task, self._request_timeout)
                    else:
                        task.result()
                except BaseException as error:
                    # finish releasing the resource before propagating recovery errors.
                    if error is not original_error:
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

            await _cleanup(
                recover_and_release(original), 2 * self._request_timeout, original
            )
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

    async def update(
        self,
        *,
        idle_timeout_seconds: Optional[int] = None,
        max_lifetime_seconds: Optional[int] = None,
    ) -> SandboxInfo:
        """update only the supplied lifetime fields and cache the returned snapshot."""
        body = {}
        if idle_timeout_seconds is not None:
            body["idleTimeoutSeconds"] = idle_timeout_seconds
        if max_lifetime_seconds is not None:
            body["maxLifetimeSeconds"] = max_lifetime_seconds
        if not body:
            raise ValueError("update requires at least one timeout")
        async with self._lifetime_lock:
            self._info = SandboxInfo.from_dict(await self._api.update(self.id, body))
            return self._info

    async def extend(self, *, seconds: int) -> SandboxInfo:
        """add seconds to the current total lifetime, not to the current time.

        this refreshes before sending an absolute lifetime to the server.
        updates through this handle are serialized, but extensions made by
        different clients are not atomic and may race.
        """
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds <= 0:
            raise ValueError("seconds must be a positive integer")
        async with self._lifetime_lock:
            info = await self.refresh()
            self._info = SandboxInfo.from_dict(
                await self._api.extend(self.id, info.max_lifetime_seconds + seconds)
            )
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
                if (
                    not isinstance(error, SandboxConflictError)
                    or error.code != "sandbox_starting"
                    or timeout == 0
                ):
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
        timeout_seconds: Optional[int] = None,
        background: bool = False,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> ExecResult:
        """execute argv, retrying only explicit 409 startup rejections.

        the startup deadline limits retries, not execution of an accepted
        command. each request retains its request timeout. transport errors,
        timeouts, and other HTTP errors are never retried because the command
        might already have executed. ``check=False`` returns command failures
        in ``ExecResult.error``, matching ``subprocess.run``. use ``check=True``
        to raise ``SandboxExecutionError`` with partial output on failure.

        ``timeout_seconds`` (1–50) requests a command limit; the backend may
        impose a shorter cap. ``request_timeout`` bounds the client http wait.
        ``background=True`` returns after starting a detached command, not its
        completion; redirect its output to a file and poll for results, or use
        ``start_command``, which keeps the output.

        ``cwd`` (an absolute path) and ``env`` (layered over the sandbox's own
        environment) apply to this command only and cannot be combined with
        ``background``. A sandbox that does not support them yet answers 422.
        """
        argv = _argv(command)
        _int_in_range(timeout_seconds, "timeout_seconds", 1, 50)
        if not isinstance(background, bool):
            raise TypeError("background must be a bool")
        cwd, env = _cwd(cwd), _env(env)
        if background and (cwd is not None or env is not None):
            raise ValueError("cwd and env cannot be combined with background")
        data = await self._ready_operation(
            lambda: self._api.exec(
                self.id,
                argv,
                timeout_seconds=timeout_seconds,
                background=background,
                cwd=cwd,
                env=env,
            ),
            startup_timeout,
        )
        result = ExecResult(
            output=data["output"],
            error=data.get("error"),
            stdout=data.get("stdout"),
            stderr=data.get("stderr"),
            exit_code=data.get("exitCode"),
            duration_ms=data.get("durationMs"),
            truncated=data.get("truncated"),
        )
        if check and (result.error is not None or result.exit_code not in (None, 0)):
            raise SandboxExecutionError(self.id, result)
        return result

    async def start_command(
        self,
        command: Sequence[str],
        *,
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout_seconds: Optional[int] = None,
        startup_timeout: Optional[float] = None,
    ) -> "AsyncioSandboxCommand":
        """Start a long-running command and return once it is running.

        Unlike ``exec``, the command keeps its output, which ``output`` and
        ``logs`` read back, and runs for up to ``timeout_seconds`` (1–86400,
        default 3600). Only a 409 ``sandbox_starting`` is retried, until the
        startup deadline. A sandbox runs at most 8 commands at once; the ninth
        start raises ``SandboxConflictError`` with code ``command_limit_reached``.
        A command that could not start is returned with status ``failed`` and
        its ``error``, not raised. Finished commands stay readable for about
        20 minutes.
        """
        argv = _argv(command)
        _int_in_range(timeout_seconds, "timeout_seconds", 1, 86400)
        cwd, env = _cwd(cwd), _env(env)
        data = await self._ready_operation(
            lambda: self._api.start_command(
                self.id, argv, timeout_seconds=timeout_seconds, cwd=cwd, env=env
            ),
            startup_timeout,
        )
        return AsyncioSandboxCommand(self, CommandInfo.from_dict(data))

    async def get_command(self, command_id: str) -> "AsyncioSandboxCommand":
        """Fetch a command started earlier in this sandbox."""
        data = await self._api.get_command(self.id, command_id)
        return AsyncioSandboxCommand(self, CommandInfo.from_dict(data))

    def logs(
        self,
        *,
        source: Optional[LogSource] = None,
        tail: Optional[int] = None,
        since: Optional[str] = None,
        last_event_id: Optional[str] = None,
        startup_timeout: Optional[float] = None,
    ) -> "AsyncioSandboxLogStream":
        """Return a lazy, closeable stream of main-process or lifecycle logs.

        This does not include exec output. Only the initial HTTP handshake is
        retried on 409; disconnects and errors after opening are never replayed.
        Use an async context or ``aclose`` when stopping iteration early.
        """
        sandbox_id = self.id
        stream = AsyncioSandboxLogStream(
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
            self._lifetime_lock = asyncio.Lock()
        except BaseException as close_error:
            if error is not None:
                raise error from close_error
            raise
        if error is not None:
            raise error


class AsyncioSandboxLogStream:
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

    async def open(self) -> "AsyncioSandboxLogStream":
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

    def __aiter__(self) -> "AsyncioSandboxLogStream":
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

    async def __aenter__(self) -> "AsyncioSandboxLogStream":
        return await self.open()

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        await _cleanup(self.aclose(), self._sandbox._request_timeout, exc)


class AsyncioSandboxCommand:
    """A command started in a sandbox; it uses the sandbox handle's connection."""

    def __init__(self, sandbox: AsyncioSandbox, info: CommandInfo) -> None:
        self._sandbox = sandbox
        self._sandbox_id = sandbox.id
        self._info = info

    @property
    def info(self) -> CommandInfo:
        """The latest snapshot; reading it does not fetch."""
        return self._info

    @property
    def id(self) -> str:
        return self._info.id

    @property
    def status(self) -> str:
        return self._info.status

    async def refresh(self) -> CommandInfo:
        """Fetch the command's current status."""
        self._info = CommandInfo.from_dict(
            await self._sandbox._api.get_command(self._sandbox_id, self.id)
        )
        return self._info

    async def cancel(self) -> CommandInfo:
        """Request a stop. The returned status can still be ``running``; ``wait`` for the end."""
        self._info = CommandInfo.from_dict(
            await self._sandbox._api.cancel_command(self._sandbox_id, self.id)
        )
        return self._info

    async def wait(
        self, *, timeout: Optional[float] = None, poll_interval: float = 1.0
    ) -> CommandInfo:
        """Poll until the status is terminal; raise ``TimeoutError`` after ``timeout`` seconds."""
        poll_interval = _timeout(poll_interval, "poll_interval")
        loop = asyncio.get_running_loop()
        deadline = (
            None
            if timeout is None
            else loop.time() + _timeout(timeout, "timeout", allow_zero=True)
        )
        while True:
            info = await self.refresh()
            if info.done:
                return info
            delay = poll_interval
            if deadline is not None:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError(
                        f"Command {self.id} is still running after {timeout:g}s"
                    )
                delay = min(delay, remaining)
            await asyncio.sleep(delay)

    async def output(
        self,
        *,
        offset: Optional[int] = None,
        tail: Optional[int] = None,
        streams: Optional[CommandStream] = None,
    ) -> CommandOutput:
        """Read one page of stored output (at most 256 KiB without ``tail``).

        Continue with ``offset=page.next_offset``. ``tail`` reads about the
        last that many bytes (up to 2 MiB). ``streams`` keeps only stdout or
        stderr, and is a 409 when ``info.streams_separated`` is false.
        """
        _offset(offset)
        _int_in_range(tail, "tail", 1, 2_097_152)
        _streams(streams)
        data = await self._sandbox._api.command_logs(
            self._sandbox_id, self.id, offset=offset, tail=tail, streams=streams
        )
        return CommandOutput.from_dict(data)

    def logs(
        self,
        *,
        offset: Optional[int] = None,
        streams: Optional[CommandStream] = None,
    ) -> "AsyncioCommandLogStream":
        """Stream output lines live until the command ends.

        After iteration, ``end`` holds the outcome. A stream the server ends
        early (after an hour, or when following output fails) leaves ``end``
        unset and ``next_offset`` to resume from with a new ``logs`` call.
        """
        _offset(offset)
        _streams(streams)
        return AsyncioCommandLogStream(
            self._sandbox._api.command_log_stream(
                self._sandbox_id, self.id, offset=offset, streams=streams
            ),
            self._sandbox._request_timeout,
        )


def _offset(offset: Optional[int]) -> None:
    if offset is not None and (
        isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
    ):
        raise ValueError("offset must be a non-negative integer")


def _streams(streams: Optional[str]) -> None:
    if streams not in (None, "stdout", "stderr"):
        raise ValueError("streams must be 'stdout' or 'stderr'")


class AsyncioCommandLogStream:
    """Typed command output lines; close it (or use ``async with``) when stopping early."""

    def __init__(self, stream: AsyncCommandLogStream, request_timeout: float) -> None:
        self._stream = stream
        self._request_timeout = request_timeout

    @property
    def end(self) -> Optional[CommandLogEnd]:
        """The command's outcome, once the stream has reached its ``end`` event."""
        end = self._stream.end
        return CommandLogEnd.from_dict(end) if end is not None else None

    @property
    def next_offset(self) -> Optional[int]:
        """Where to resume when the server ended the stream before the command ended."""
        return self._stream.next_offset

    @property
    def error(self) -> Optional[str]:
        """Why the server ended the stream early, when it said."""
        return self._stream.error

    async def open(self) -> "AsyncioCommandLogStream":
        """Complete the HTTP handshake without waiting for the first line."""
        await self._stream.open()
        return self

    def __aiter__(self) -> "AsyncioCommandLogStream":
        return self

    async def __anext__(self) -> CommandLogLine:
        return CommandLogLine.from_dict(await self._stream.__anext__())

    async def aclose(self) -> None:
        await self._stream.aclose()

    async def __aenter__(self) -> "AsyncioCommandLogStream":
        return await self.open()

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        await _cleanup(self.aclose(), self._request_timeout, exc)
