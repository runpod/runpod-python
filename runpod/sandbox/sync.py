"""Blocking sandbox facade backed by one stable asyncio loop per active handle."""

import asyncio
import threading
from concurrent.futures import Future
from datetime import datetime
from typing import Any, Coroutine, Iterator, Mapping, Optional, Sequence, TypeVar

from runpod.sandbox.asyncio import AsyncioSandbox
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
        self._lock = threading.Lock()

    @property
    def started(self) -> bool:
        return self._thread is not None

    def _start(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                ready = threading.Event()

                def serve() -> None:
                    loop = asyncio.new_event_loop()
                    self._loop = loop
                    ready.set()
                    try:
                        loop.run_forever()
                    finally:
                        loop.run_until_complete(loop.shutdown_asyncgens())
                        loop.run_until_complete(loop.shutdown_default_executor())
                        loop.close()

                self._thread = threading.Thread(
                    target=serve, name="runpod-sandbox", daemon=True
                )
                self._thread.start()
                ready.wait()
            assert self._loop is not None
            return self._loop

    def run(self, coroutine: Coroutine[Any, Any, _T]) -> _T:
        if threading.current_thread() is self._thread:
            coroutine.close()
            raise RuntimeError("Cannot call the sync sandbox API from its own loop")
        loop = self._start()
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
            task.add_done_callback(complete)

        def cancel() -> None:
            if task is not None:
                task.cancel()

        loop.call_soon_threadsafe(submit)
        try:
            return future.result()
        except BaseException:
            if not future.done():
                # Cancelling a concurrent Future marks it done before the async
                # task's finally blocks finish. Wait for the actual task before
                # stopping its loop, or Ctrl-C can strand a newly created sandbox.
                loop.call_soon_threadsafe(cancel)
                while not finished.is_set():
                    try:
                        finished.wait()
                    except KeyboardInterrupt:
                        # Cleanup is bounded by the async domain's timeouts.
                        continue
            raise

    def stop(self) -> None:
        with self._lock:
            loop, thread = self._loop, self._thread
            if loop is None or thread is None:
                return

            async def drain() -> None:
                current = asyncio.current_task()
                tasks = [task for task in asyncio.all_tasks() if task is not current]
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

            try:
                asyncio.run_coroutine_threadsafe(drain(), loop).result()
            finally:
                loop.call_soon_threadsafe(loop.stop)
                thread.join()
                self._loop = None
                self._thread = None


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

    ``with Sandbox(image_name=...)`` creates on entry and terminates on exit.
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
        self._runner = _LoopRunner()
        self._entered = False
        self._sandbox = AsyncioSandbox(
            image_name=image_name,
            template_id=template_id,
            name=name,
            cpu_flavor_id=cpu_flavor_id,
            vcpu_count=vcpu_count,
            memory_in_gb=memory_in_gb,
            data_center_id=data_center_id,
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
        return instance

    @classmethod
    def create(cls, **options: Any) -> "Sandbox":
        """Create immediately; the returned snapshot may still be CREATING."""
        sandbox = cls(**options)
        try:
            sandbox._runner.run(sandbox._sandbox._create())
        except BaseException:
            sandbox.close()
            raise
        return sandbox

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
        try:
            sandbox = runner.run(
                AsyncioSandbox.get(
                    sandbox_id,
                    api_key=api_key,
                    base_url=base_url,
                    request_timeout=request_timeout,
                    startup_timeout=startup_timeout,
                )
            )
        except BaseException:
            runner.stop()
            raise
        return cls._from_async(sandbox, runner)

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

    def exec(
        self,
        command: Sequence[str],
        *,
        check: bool = False,
        startup_timeout: Optional[float] = None,
    ) -> ExecResult:
        """Execute argv, waiting only for explicit startup rejections."""
        return self._runner.run(
            self._sandbox.exec(command, check=check, startup_timeout=startup_timeout)
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

    def __enter__(self) -> "Sandbox":
        if self._entered:
            raise RuntimeError("Sandbox context is already entered")
        try:
            self._runner.run(self._sandbox.__aenter__())
        except BaseException:
            self._runner.stop()
            raise
        self._entered = True
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        try:
            self._runner.run(self._sandbox.__aexit__(exc_type, exc, traceback))
            return False
        finally:
            self._entered = False
            self._runner.stop()
