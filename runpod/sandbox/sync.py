"""Blocking sandbox facade backed by one stable asyncio loop per active handle."""

import asyncio
import threading
from concurrent.futures import Future
from datetime import datetime
from typing import (
    Any,
    Coroutine,
    Iterator,
    Literal,
    Mapping,
    Optional,
    Sequence,
    TypeVar,
)

from runpod.apps.volume import Volume
from runpod.sandbox.asyncio import AsyncioSandbox, _cleanup
from .models import (
    ExecResult,
    LogEvent,
    LogSource,
    SandboxCompute,
    SandboxInfo,
    SandboxState,
)

_T = TypeVar("_T")


class _LoopRunner:
    """Keep aiohttp sessions on their original loop, including across log reads."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._stopping = False
        self._stopped = threading.Event()
        self._stopped.set()
        self._loop_finished = threading.Event()
        self._tasks: dict[asyncio.Task, bool] = {}

    @property
    def started(self) -> bool:
        return self._thread is not None

    def _start(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                ready = threading.Event()
                self._loop_finished.clear()

                def serve() -> None:
                    loop = asyncio.new_event_loop()
                    self._loop = loop
                    ready.set()
                    try:
                        loop.run_forever()
                    finally:
                        try:
                            loop.run_until_complete(loop.shutdown_asyncgens())
                            loop.run_until_complete(loop.shutdown_default_executor())
                            loop.close()
                        finally:
                            self._loop_finished.set()

                self._thread = threading.Thread(
                    target=serve, name="runpod-sandbox", daemon=True
                )
                self._thread.start()
                ready.wait()
            assert self._loop is not None
            return self._loop

    def run(
        self, coroutine: Coroutine[Any, Any, _T], *, cleanup: bool = False
    ) -> _T:
        if threading.current_thread() is self._thread:
            coroutine.close()
            raise RuntimeError("Cannot call the sync sandbox API from its own loop")
        loop: asyncio.AbstractEventLoop
        future: Future[_T] = Future()
        finished = threading.Event()
        task: Optional[asyncio.Task[tuple[Optional[_T], Optional[BaseException]]]] = (
            None
        )
        invoked = False

        async def invoke() -> tuple[Optional[_T], Optional[BaseException]]:
            nonlocal invoked
            invoked = True
            try:
                return await coroutine, None
            except BaseException as error:
                # interrupts belong to the calling thread, not the background loop.
                return None, error

        def complete(done: asyncio.Task) -> None:
            self._tasks.pop(done, None)
            try:
                result, error = done.result()
                if error is not None:
                    future.set_exception(error)
                else:
                    future.set_result(result)
            except BaseException as error:
                # the waiting caller receives the task's cancellation or failure.
                future.set_exception(error)
            finally:
                if not invoked:
                    coroutine.close()
                finished.set()

        def submit() -> None:
            nonlocal task
            task = loop.create_task(invoke())
            self._tasks[task] = cleanup
            task.add_done_callback(complete)

        def cancel() -> None:
            if task is not None:
                task.cancel()

        try:
            while True:
                with self._lock:
                    if not self._stopping:
                        loop = self._start()
                        loop.call_soon_threadsafe(submit)
                        break
                    if not cleanup:
                        raise RuntimeError("Sandbox loop is shutting down")
                    stopped = self._stopped
                while not stopped.is_set():
                    try:
                        stopped.wait()
                    except KeyboardInterrupt:
                        # owner cleanup must survive repeated interruptions.
                        continue
        except BaseException:
            coroutine.close()
            raise
        try:
            return future.result()
        except BaseException as original:
            with self._lock:
                if not cleanup and not finished.is_set() and not self._stopping:
                    loop.call_soon_threadsafe(cancel)
            while not finished.is_set():
                try:
                    finished.wait()
                except KeyboardInterrupt:
                    # async cleanup owns its child tasks and bounds their lifetime.
                    continue
            error = future.exception()
            if error is not None and error is not original:
                raise original from error
            raise

    def stop(self) -> None:
        if threading.current_thread() is self._thread:
            raise RuntimeError("Cannot stop the sync sandbox API from its own loop")
        with self._lock:
            if self._stopping:
                stopped = self._stopped
                shutdown = None
            else:
                loop, thread = self._loop, self._thread
                if loop is None or thread is None:
                    return
                self._stopping = True
                stopped = self._stopped = threading.Event()

                async def drain() -> None:
                    # cancel invocations, not the protected cleanup tasks they own.
                    tasks = tuple(self._tasks)
                    for task in tasks:
                        if not self._tasks[task]:
                            task.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)

                shutdown = asyncio.run_coroutine_threadsafe(drain(), loop)
        interrupted = None
        if shutdown is None:
            while not stopped.is_set():
                try:
                    stopped.wait()
                except KeyboardInterrupt as error:
                    interrupted = error
        else:
            try:
                while True:
                    try:
                        shutdown.result()
                        break
                    except KeyboardInterrupt as error:
                        interrupted = error
            finally:
                loop.call_soon_threadsafe(loop.stop)
                while not self._loop_finished.is_set():
                    try:
                        self._loop_finished.wait()
                    except KeyboardInterrupt as error:
                        interrupted = error
                with self._lock:
                    self._loop = None
                    self._thread = None
                    self._stopping = False
                    stopped.set()
        if interrupted is not None:
            raise interrupted


class SandboxLogs(Iterator[LogEvent]):
    """Closeable log iterator. Use a with block when stopping consumption early."""

    def __init__(self, stream: Any, runner: _LoopRunner) -> None:
        self._stream = stream
        self._runner = runner
        self._closed = False

    def __enter__(self) -> "SandboxLogs":
        if self._closed:
            raise RuntimeError("Log stream is closed")
        self._runner.run(self._stream.__aenter__())
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self._closed = True
        if self._runner.started:
            self._runner.run(self._stream.__aexit__(exc_type, exc, traceback))
        return False

    def __iter__(self) -> "SandboxLogs":
        return self

    def __next__(self) -> LogEvent:
        if self._closed:
            raise StopIteration
        try:
            return self._runner.run(self._stream.__anext__())
        except StopAsyncIteration:
            self._closed = True
            raise StopIteration from None
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._runner.started:
                self._runner.run(self._stream.aclose())


class Sandbox:
    """An isolated CPU sandbox with blocking methods.

    ``with Sandbox(image=...)`` creates on entry and terminates on exit.
    ``create`` leaves the lifetime under your control; call ``terminate`` when
    finished, or ``close`` to release only local connections. Handles retrieved
    with ``get`` or ``list`` never terminate compute on context exit.

    A stable background event loop runs the async implementation. No thread or
    connection is started by the constructor. Prefer AsyncioSandbox in async
    applications so blocking calls do not pause the application's event loop.
    """

    def __init__(
        self,
        *,
        image: Optional[str] = None,
        template_id: Optional[str] = None,
        name: Optional[str] = None,
        cpu_flavor_id: Optional[str] = None,
        vcpu_count: Optional[int] = None,
        memory_in_gb: Optional[int] = None,
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
        self._runner = _LoopRunner()
        self._entered = False
        self._context_lock = threading.Lock()
        self._sandbox = AsyncioSandbox(
            image=image,
            template_id=template_id,
            name=name,
            cpu_flavor_id=cpu_flavor_id,
            vcpu_count=vcpu_count,
            memory_in_gb=memory_in_gb,
            disk_gb=disk_gb,
            data_center_ids=data_center_ids,
            mounts=mounts,
            ports=ports,
            cmd=cmd,
            entrypoint=entrypoint,
            start_ssh=start_ssh,
            registry_auth=registry_auth,
            env=env,
            idle_timeout_seconds=idle_timeout_seconds,
            max_lifetime_seconds=max_lifetime_seconds,
            labels=labels,
            api_key=api_key,
            base_url=base_url,
            request_timeout=request_timeout,
            startup_timeout=startup_timeout,
        )

    @classmethod
    def _from_async(
        cls, sandbox: AsyncioSandbox, runner: Optional[_LoopRunner] = None
    ) -> "Sandbox":
        instance = cls.__new__(cls)
        instance._sandbox = sandbox
        instance._runner = runner if runner is not None else _LoopRunner()
        instance._entered = False
        instance._context_lock = threading.Lock()
        return instance

    @classmethod
    def create(cls, **options: Any) -> "Sandbox":
        """Create immediately; the returned snapshot may still be CREATING."""
        sandbox = cls(**options)
        completed = False

        async def create() -> AsyncioSandbox:
            nonlocal completed
            await sandbox._sandbox._create()
            completed = True
            return sandbox._sandbox

        try:
            sandbox._runner.run(create())
            return sandbox
        except BaseException as original:
            sandbox._abort_entry(original, completed)
            raise

    @classmethod
    def get(
        cls,
        sandbox_id: str,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        request_timeout: float = 30,
        startup_timeout: float = 60,
    ) -> "Sandbox":
        """Fetch a borrowed handle; closing its context does not terminate it."""
        runner = _LoopRunner()
        sandbox = None

        async def get() -> AsyncioSandbox:
            nonlocal sandbox
            sandbox = await AsyncioSandbox.get(
                sandbox_id,
                api_key=api_key,
                base_url=base_url,
                request_timeout=request_timeout,
                startup_timeout=startup_timeout,
            )
            return sandbox

        try:
            result = runner.run(get())
            return cls._from_async(result, runner)
        except BaseException as original:
            if sandbox is not None:
                cls._from_async(sandbox, runner)._abort_entry(original, True)
            else:
                try:
                    runner.stop()
                except BaseException as error:
                    raise original from error
            raise

    @classmethod
    def list(
        cls,
        *,
        state: Optional[SandboxState] = None,
        labels: Optional[Mapping[str, str]] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        request_timeout: float = 30,
        startup_timeout: float = 60,
    ) -> list["Sandbox"]:
        """List borrowed handles. Filters combine with AND on the server."""
        runner = _LoopRunner()
        try:
            sandboxes = runner.run(
                AsyncioSandbox.list(
                    state=state,
                    labels=labels,
                    api_key=api_key,
                    base_url=base_url,
                    request_timeout=request_timeout,
                    startup_timeout=startup_timeout,
                )
            )
        finally:
            runner.stop()
        return [cls._from_async(sandbox) for sandbox in sandboxes]

    @property
    def info(self) -> SandboxInfo:
        return self._sandbox.info

    @property
    def id(self) -> str:
        return self._sandbox.id

    @property
    def state(self) -> SandboxState:
        return self._sandbox.state

    @property
    def compute(self) -> Optional[SandboxCompute]:
        return self._sandbox.compute

    @property
    def expires_at(self) -> datetime:
        return self._sandbox.expires_at

    def refresh(self) -> SandboxInfo:
        """Fetch current server metadata and replace the local snapshot."""
        return self._runner.run(self._sandbox.refresh())

    def update(
        self,
        *,
        idle_timeout_seconds: Optional[int] = None,
        max_lifetime_seconds: Optional[int] = None,
    ) -> SandboxInfo:
        """update the supplied timeout fields and return the refreshed snapshot."""
        return self._runner.run(
            self._sandbox.update(
                idle_timeout_seconds=idle_timeout_seconds,
                max_lifetime_seconds=max_lifetime_seconds,
            )
        )

    def extend(self, *, seconds: int) -> SandboxInfo:
        """add to the current total lifetime; not atomic across separate clients."""
        return self._runner.run(self._sandbox.extend(seconds=seconds))

    def exec(
        self,
        command: Sequence[str],
        *,
        check: bool = False,
        startup_timeout: Optional[float] = None,
        timeout_seconds: Optional[int] = None,
        background: bool = False,
    ) -> ExecResult:
        """Execute argv, waiting only for explicit startup rejections."""
        return self._runner.run(
            self._sandbox.exec(
                command,
                check=check,
                startup_timeout=startup_timeout,
                timeout_seconds=timeout_seconds,
                background=background,
            )
        )

    def logs(
        self,
        *,
        source: Optional[LogSource] = None,
        tail: Optional[int] = None,
        since: Optional[str] = None,
        last_event_id: Optional[str] = None,
        startup_timeout: Optional[float] = None,
    ) -> SandboxLogs:
        """Stream container/system logs, not exec output; close on early exit."""
        return SandboxLogs(
            self._sandbox.logs(
                source=source,
                tail=tail,
                since=since,
                last_event_id=last_event_id,
                startup_timeout=startup_timeout,
            ),
            self._runner,
        )

    def terminate(self) -> None:
        """Release remote compute and local connections; safe to repeat."""
        try:
            self._runner.run(self._sandbox.terminate())
        finally:
            self._runner.stop()

    def close(self) -> None:
        """Release local connections and the loop, without terminating compute."""
        if self._runner.started:
            try:
                self._runner.run(self._sandbox.close())
            finally:
                self._runner.stop()

    def _abort_entry(self, original: BaseException, completed: bool) -> None:
        async def release() -> None:
            try:
                # shutdown may have closed the session's loop before handoff.
                await _cleanup(self._sandbox.close(), self._sandbox._request_timeout)
            finally:
                if completed:
                    await self._sandbox.__aexit__(
                        type(original), original, original.__traceback__
                    )

        try:
            try:
                self._runner.run(release(), cleanup=True)
            finally:
                self._runner.stop()
        except BaseException as error:
            if error is not original:
                raise original from error
            raise

    def __enter__(self) -> "Sandbox":
        with self._context_lock:
            if self._entered:
                raise RuntimeError("Sandbox context is already entered")
            self._entered = True
        completed = False

        async def enter() -> AsyncioSandbox:
            nonlocal completed
            result = await self._sandbox.__aenter__()
            completed = True
            return result

        try:
            self._runner.run(enter())
            return self
        except BaseException as original:
            try:
                self._abort_entry(original, completed)
            finally:
                with self._context_lock:
                    self._entered = False
            raise

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        try:
            self._runner.run(self._sandbox.__aexit__(exc_type, exc, traceback))
            return False
        finally:
            try:
                self._runner.stop()
            finally:
                with self._context_lock:
                    self._entered = False
