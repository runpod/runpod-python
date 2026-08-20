"""Queue prestart lifecycle coverage: handler gate, failure delivery, drain, and exit."""

# pylint: disable=protected-access

import asyncio
import functools
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from runpod.serverless.modules import rp_capture, rp_prestart, rp_scale
from runpod.serverless.modules.rp_prestart import (
    PrestartError,
    PrestartTimeout,
    build_prestart_failed_payload,
    run_prestart_hooks_async,
    run_prestart_phase,
)
from runpod.serverless.modules.rp_scale import JobScaler


def _run(coro):
    return asyncio.run(coro)


class TestRunPrestartHooksAsync(unittest.TestCase):
    """Runs ordered hooks to completion or raises a named startup failure."""

    def test_sync_success_offloaded(self):
        calls = []
        _run(run_prestart_hooks_async((lambda: calls.append("ran"),)))
        assert calls == ["ran"]

    def test_sync_callable_returning_awaitable_is_awaited(self):
        state = {}

        async def load():
            await asyncio.sleep(0)
            state["ready"] = True

        def initialize():
            return load()

        _run(run_prestart_hooks_async((initialize,)))
        assert state == {"ready": True}

    def test_sync_failure_wraps_in_prestart_error(self):
        def failing_hook():
            raise ValueError("max_model_len must be positive, got 0")

        with self.assertRaises(PrestartError) as ctx:
            _run(run_prestart_hooks_async((failing_hook,)))
        assert isinstance(ctx.exception.original, ValueError)
        assert "max_model_len" in str(ctx.exception)

    def test_async_success(self):
        state = {}

        async def load():
            await asyncio.sleep(0)
            state["ready"] = True

        _run(run_prestart_hooks_async((load,)))
        assert state == {"ready": True}

    def test_async_failure_wraps_in_prestart_error(self):
        async def failing_hook():
            raise RuntimeError("CUDA OOM")

        with self.assertRaises(PrestartError) as ctx:
            _run(run_prestart_hooks_async((failing_hook,)))
        assert isinstance(ctx.exception.original, RuntimeError)

    def test_hook_timeout_error_is_not_phase_timeout(self):
        async def failing_hook():
            raise asyncio.TimeoutError("backend request timed out")

        with self.assertRaises(PrestartError) as ctx:
            _run(run_prestart_hooks_async((failing_hook,), timeout=1))
        assert isinstance(ctx.exception.original, asyncio.TimeoutError)

    def test_async_timeout_raises_prestart_timeout(self):
        async def slow():
            await asyncio.sleep(3)

        with self.assertRaises(PrestartTimeout):
            _run(run_prestart_hooks_async((slow,), timeout=1))

    def test_zero_timeout_raises_prestart_timeout(self):
        async def load():
            await asyncio.sleep(0)

        with self.assertRaises(PrestartTimeout):
            _run(run_prestart_hooks_async((load,), timeout=0))

    def test_sync_hang_times_out(self):
        """A blocking sync hook is cut off by prestart_timeout, not left stuck."""
        release = threading.Event()

        def hang():
            release.wait(10)

        try:
            with self.assertRaises(PrestartTimeout):
                _run(run_prestart_hooks_async((hang,), timeout=1))
        finally:
            release.set()  # let the offloaded thread exit promptly

    def test_async_partial_is_awaited(self):
        """A partial wrapping an async hook is detected and awaited."""
        state = {}

        async def load(key):
            await asyncio.sleep(0)
            state[key] = True

        _run(run_prestart_hooks_async((functools.partial(load, "ready"),)))
        assert state == {"ready": True}

    def test_async_callable_object_is_awaited(self):
        """An object whose __call__ is async is detected and awaited."""
        state = {}

        class Loader:
            async def __call__(self):
                await asyncio.sleep(0)
                state["ready"] = True

        _run(run_prestart_hooks_async((Loader(),)))
        assert state == {"ready": True}

    def test_base_exception_propagates_unwrapped(self):
        """KeyboardInterrupt is process control, not a prestart failure."""

        def interrupted():
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            _run(run_prestart_hooks_async((interrupted,)))

    def test_system_exit_wraps_as_prestart_error(self):
        """A hook calling sys.exit() is a failure to surface with a reason,
        not a clean exit that abandons held jobs."""

        def bails():
            raise SystemExit(1)

        with self.assertRaises(PrestartError):
            _run(run_prestart_hooks_async((bails,)))


class TestPrestartFailedSignal(unittest.TestCase):
    """Builds the structured prestart_failed payload, including captured logs."""

    def test_payload_shape_from_sync_error(self):
        try:
            raise ValueError("bad config")
        except ValueError as exc:
            payload = build_prestart_failed_payload(
                PrestartError(exc, "load_model"), logs="stderr tail"
            )
        assert payload["event"] == "prestart_failed"
        assert payload["error_type"] == "ValueError"
        assert payload["error_message"] == "bad config"
        assert "ValueError" in payload["error_traceback"]
        assert payload["logs"] == "stderr tail"
        assert payload["hook"] == "load_model"
        assert "worker_id" in payload and "runpod_version" in payload

    def test_payload_omits_empty_logs(self):
        payload = build_prestart_failed_payload(PrestartError(ValueError("x"), "hook"))
        assert "logs" not in payload

    def test_payload_bounds_message_traceback_and_logs(self):
        huge = "x" * (rp_capture.MAX_CAPTURED_CHARS * 3)
        payload = build_prestart_failed_payload(
            PrestartError(ValueError(huge), "hook"),
            logs="y" * (rp_capture.MAX_CAPTURED_CHARS * 3),
        )
        assert len(payload["error_message"]) <= rp_capture.MAX_CAPTURED_CHARS + 100
        assert len(payload["error_traceback"]) <= rp_capture.MAX_CAPTURED_CHARS + 100
        assert len(payload["logs"]) <= rp_capture.MAX_CAPTURED_CHARS


def _scaler(hook=None):
    scaler = JobScaler({"handler": lambda j: j, "rp_args": {}})
    scaler.prestart_hooks = () if hook is None else (hook,)
    scaler.job_progress = MagicMock()  # avoid the process-wide singleton in unit tests
    scaler.job_progress.get_job_count.return_value = 0
    return scaler


class TestMultipleHooks(unittest.TestCase):
    """With several hooks under one phase, the failure has to say which hook broke
    and carry what the earlier ones printed on the way there."""

    def test_capture_spans_every_hook_and_names_the_one_that_failed(self):
        def download():
            print("downloaded weights")

        async def warm():
            print("warmed cache")

        def start_engine():
            print("starting engine")
            raise RuntimeError("engine refused to start")

        real = io.StringIO()
        with patch.object(sys, "stdout", rp_capture._TeeProxy(real)):
            payload = _run(run_prestart_phase((download, warm, start_engine)))

        assert payload["hook"] == "start_engine"
        assert payload["error_message"] == "engine refused to start"
        # The whole phase shares one buffer, so earlier hooks are still in scope.
        assert "downloaded weights" in payload["logs"]
        assert "warmed cache" in payload["logs"]
        assert "starting engine" in payload["logs"]

    def test_a_later_hook_does_not_run_after_an_earlier_one_fails(self):
        calls = []

        def first():
            calls.append("first")

        def second():
            calls.append("second")
            raise RuntimeError("stop here")

        def third():
            calls.append("third")

        payload = _run(run_prestart_phase((first, second, third)))

        assert calls == ["first", "second"]
        assert payload["hook"] == "second"


class TestRunPrestart(unittest.TestCase):
    """Prestart opens the handler gate or records the failure and shuts down."""

    def test_no_hooks_opens_gate_immediately(self):
        scaler = _scaler(hook=None)
        _run(scaler._run_prestart())
        assert scaler._prestart_ready.is_set()
        assert scaler._prestart_error is None

    def test_success_opens_gate_no_error(self):
        ran = []
        scaler = _scaler(hook=lambda: ran.append(1))
        _run(scaler._run_prestart())
        assert ran == [1]
        assert scaler._prestart_ready.is_set()
        assert scaler._prestart_error is None
        assert not scaler._shutdown_event.is_set()

    def test_failure_records_hook_reason_with_logs_and_shuts_down(self):
        def failing_hook():
            print("downloading model")
            raise RuntimeError("CUDA OOM: model too big")

        scaler = _scaler(hook=failing_hook)
        real = io.StringIO()
        with (
            patch.object(sys, "stdout", rp_capture._TeeProxy(real)),
            patch.object(rp_prestart, "log") as mock_log,
        ):
            _run(scaler._run_prestart())

        assert (
            scaler._prestart_ready.is_set()
        )  # held handlers are released to fail fast
        assert scaler._prestart_error is not None
        assert scaler._prestart_error["error_message"] == "CUDA OOM: model too big"
        assert scaler._prestart_error["hook"] == "failing_hook"
        assert "downloading model" in scaler._prestart_error["logs"]
        assert any(
            call.args[0].startswith("prestart_failed | ")
            for call in mock_log.error.call_args_list
        )
        assert (
            scaler._shutdown_event.is_set()
        )  # broken worker shuts down (occupancy was 0)

    def test_failure_starts_shutdown_before_drain(self):
        scaler = _scaler(hook=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        shutdown_states = []

        def occupancy():
            shutdown_states.append(scaler._shutdown_event.is_set())
            return 0

        scaler.current_occupancy = occupancy
        _run(scaler._run_prestart())

        assert shutdown_states == [True]


class TestHandleJobGate(unittest.TestCase):
    """The handler waits for prestart; failure fails the in-hand request."""

    def _prime(self, scaler, job):
        # Balance the queue/progress bookkeeping handle_job's finally expects.
        scaler.jobs_queue = asyncio.Queue(maxsize=4)
        scaler.jobs_queue.put_nowait(job)

    def test_runs_handler_once_prestart_is_ready(self):
        scaler = _scaler(hook=lambda: None)
        scaler.jobs_handler = AsyncMock()
        scaler._prestart_ready.set()
        job = {"id": "j1"}

        async def go():
            self._prime(scaler, job)
            await scaler.handle_job(None, job)

        _run(go())
        scaler.jobs_handler.assert_awaited_once()

    def test_handler_stays_blocked_until_prestart_completes(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked_hook():
            started.set()
            await release.wait()

        scaler = _scaler(hook=blocked_hook)
        scaler.jobs_handler = AsyncMock()
        job = {"id": "blocked"}

        async def go():
            self._prime(scaler, job)
            prestart_task = asyncio.create_task(scaler._run_prestart())
            await started.wait()
            handler_task = asyncio.create_task(scaler.handle_job(None, job))
            await asyncio.sleep(0)
            scaler.jobs_handler.assert_not_awaited()

            release.set()
            await asyncio.gather(prestart_task, handler_task)

        _run(go())
        scaler.jobs_handler.assert_awaited_once()

    def test_runs_handler_without_hooks(self):
        scaler = _scaler(hook=None)
        scaler.jobs_handler = AsyncMock()
        job = {"id": "j2"}

        async def go():
            self._prime(scaler, job)
            await scaler.handle_job(None, job)

        _run(go())
        scaler.jobs_handler.assert_awaited_once()  # no gate to wait on

    def test_prestart_failure_fails_request_without_running_handler(self):
        scaler = _scaler(hook=lambda: None)
        scaler.jobs_handler = AsyncMock()
        scaler._prestart_error = {
            "error_message": "CUDA OOM",
            "event": "prestart_failed",
        }
        scaler._prestart_ready.set()
        job = {"id": "j3"}

        async def go():
            self._prime(scaler, job)
            with patch.object(rp_scale, "send_result", new=AsyncMock()) as mock_sr:
                await scaler.handle_job(None, job)
                mock_sr.assert_awaited_once()
                sent = mock_sr.await_args[0][1]
                assert json.loads(sent["error"])["error_message"] == "CUDA OOM"

        _run(go())
        scaler.jobs_handler.assert_not_awaited()  # broken worker never runs the handler

    def test_prestart_failure_before_any_take_claims_a_request_to_fail(self):
        """An instant failure leaves no request in hand and starts shutdown.
        The worker must claim one request and fail it, or the caller waits out
        the queue TTL without receiving the startup reason."""
        scaler = _scaler(hook=lambda: None)
        scaler._prestart_error = {
            "error_message": "CUDA OOM",
            "event": "prestart_failed",
        }
        scaler.kill_worker()  # _run_prestart sets shutdown on failure
        scaler._fail_job = AsyncMock()
        job = {"id": "orphan-1"}
        scaler.jobs_fetcher = AsyncMock(return_value=[job])

        _run(asyncio.wait_for(scaler.get_jobs(AsyncMock()), timeout=0.5))

        scaler.jobs_fetcher.assert_awaited_once()
        scaler._fail_job.assert_awaited_once()
        assert scaler._fail_job.await_args[0][1] is job
        assert scaler.jobs_queue.qsize() == 0  # never queued into a broken worker

    def test_prestart_failure_with_empty_queue_exits_without_hanging(self):
        """An empty claim attempt lets job-take stop without hanging."""
        scaler = _scaler(hook=lambda: None)
        scaler._prestart_error = {
            "error_message": "CUDA OOM",
            "event": "prestart_failed",
        }
        scaler.kill_worker()
        scaler._fail_job = AsyncMock()
        scaler.jobs_fetcher = AsyncMock(return_value=[])

        _run(asyncio.wait_for(scaler.get_jobs(AsyncMock()), timeout=0.5))

        scaler.jobs_fetcher.assert_awaited_once()
        scaler._fail_job.assert_not_awaited()

    def test_prestart_failure_after_a_take_does_not_claim_another_request(self):
        """Once this worker claims a request, report the failure against it.
        Claiming another would fail work that a healthy worker could serve."""
        scaler = _scaler(hook=lambda: None)
        scaler._fail_job = AsyncMock()
        failure = {"error_message": "CUDA OOM", "event": "prestart_failed"}
        calls = []

        async def fetcher(_session, _needed):
            calls.append(1)
            return [{"id": f"job-{len(calls)}"}]

        scaler.jobs_fetcher = fetcher

        async def go():
            task = asyncio.create_task(scaler.get_jobs(AsyncMock()))
            await asyncio.sleep(0)
            scaler._prestart_error = failure  # lands after the first take succeeded
            scaler.kill_worker()
            await asyncio.wait_for(task, timeout=0.5)

        _run(go())

        assert len(calls) == 1  # no extra claim after the queued request

    def test_instant_async_failure_still_fails_the_queued_request(self):
        """An async hook can fail before job-take fetches anything. The real
        prestart failure path must still claim and fail the queued request."""

        async def bad_hook():
            raise RuntimeError("bad config")

        scaler = _scaler(hook=bad_hook)
        scaler._fail_job = AsyncMock()
        job = {"id": "queued-1"}
        scaler.jobs_fetcher = AsyncMock(return_value=[job])
        scaler.jobs_handler = AsyncMock()

        async def go():
            await scaler._run_prestart()  # records the reason and sets shutdown
            assert not scaler.is_alive()
            await asyncio.wait_for(scaler.get_jobs(AsyncMock()), timeout=1)

        _run(go())

        scaler._fail_job.assert_awaited_once()
        assert scaler._fail_job.await_args[0][1] is job
        reason = scaler._fail_job.await_args[0][2]
        assert reason["event"] == "prestart_failed"
        assert reason["error_message"] == "bad config"
        scaler.jobs_handler.assert_not_awaited()  # handler never runs on a broken worker

    def test_claim_attempt_is_bounded(self):
        """A silent job-take must not hold a dying worker open. The claim gives up on
        its own bound and the worker exits to be respawned."""
        scaler = _scaler(hook=lambda: None)
        scaler._prestart_error = {
            "error_message": "CUDA OOM",
            "event": "prestart_failed",
        }
        scaler.kill_worker()
        scaler._fail_job = AsyncMock()
        scaler.prestart_claim_timeout = 0.05

        async def never_returns(_session, _needed):
            await asyncio.Event().wait()

        scaler.jobs_fetcher = never_returns

        _run(asyncio.wait_for(scaler.get_jobs(AsyncMock()), timeout=1))

        scaler._fail_job.assert_not_awaited()

    def test_jobs_acquired_after_prestart_failure_are_failed_not_queued(self):
        """If prestart fails during a long-poll, fail returned jobs and stop
        job-take without relying on another task to end the loop."""
        scaler = _scaler(hook=lambda: None)
        scaler._fail_job = AsyncMock()
        job = {"id": "late-1"}
        failure = {"error_message": "CUDA OOM", "event": "prestart_failed"}

        async def fetcher(_session, _needed):
            # Prestart failure lands while this long-poll is in flight.
            scaler._prestart_error = failure
            return [job]

        scaler.jobs_fetcher = fetcher
        _run(asyncio.wait_for(scaler.get_jobs(AsyncMock()), timeout=0.5))
        scaler._fail_job.assert_awaited_once()
        assert scaler._fail_job.await_args[0][1] is job
        assert scaler.jobs_queue.qsize() == 0
        scaler.job_progress.add.assert_not_called()

    def test_shutdown_before_prestart_ready_leaves_request_alone(self):
        """SIGTERM during prestart releases a held request without running the
        handler against an uninitialized worker."""
        scaler = _scaler(hook=lambda: None)
        scaler.jobs_handler = AsyncMock()
        scaler.kill_worker()
        job = {"id": "j4"}

        async def go():
            self._prime(scaler, job)
            with patch.object(rp_scale, "send_result", new=AsyncMock()) as mock_sr:
                await asyncio.wait_for(scaler.handle_job(None, job), timeout=0.5)
                mock_sr.assert_not_awaited()  # the platform retries it elsewhere

        _run(go())
        scaler.jobs_handler.assert_not_awaited()


class TestShutdownWithHangingPrestart(unittest.TestCase):
    """A hung hook without a timeout must not outlive worker shutdown."""

    def test_run_returns_while_hook_is_still_blocked(self):
        release = threading.Event()
        self.addCleanup(release.set)

        scaler = _scaler(hook=release.wait)
        scaler.kill_worker()
        scaler.stop_signals_fetcher = AsyncMock(return_value=[])

        _run(asyncio.wait_for(scaler.run(), timeout=2))

        assert not release.is_set()  # still blocked, and the worker left anyway

    def test_process_control_exception_stops_queue_loops_and_propagates(self):
        class StopWorker(BaseException):
            pass

        async def interrupted():
            raise StopWorker("stop")

        async def blocked(_session, *_args):
            await asyncio.Event().wait()

        scaler = _scaler(hook=interrupted)
        scaler.jobs_fetcher = blocked
        scaler.stop_signals_fetcher = blocked
        scaler.jobs_handler = AsyncMock()

        with self.assertRaisesRegex(StopWorker, "stop"):
            _run(asyncio.wait_for(scaler.run(), timeout=0.5))

        scaler.jobs_handler.assert_not_awaited()

    def test_request_loop_failure_cancels_blocked_sibling_loops(self):
        """A failed request loop must not outlive its shared HTTP session."""
        take_started = asyncio.Event()
        stop_started = asyncio.Event()
        take_stopped = asyncio.Event()
        stop_stopped = asyncio.Event()

        async def blocked_take(_session):
            take_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                take_stopped.set()

        async def blocked_stop(_session):
            stop_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stop_stopped.set()

        async def failed_run_jobs(_session):
            await asyncio.gather(take_started.wait(), stop_started.wait())
            raise RuntimeError("request loop failed")

        scaler = _scaler(hook=lambda: None)
        scaler.get_jobs = blocked_take
        scaler.run_jobs = failed_run_jobs
        scaler.monitor_stop_signals = blocked_stop

        with self.assertRaisesRegex(RuntimeError, "request loop failed"):
            _run(asyncio.wait_for(scaler.run(), timeout=0.5))

        assert take_stopped.is_set()
        assert stop_stopped.is_set()


# Needs a real process: in-process the interpreter never joins threads, so the hang
# this guards against cannot be observed.
_HARD_EXIT_SCRIPT = """
import asyncio, sys, threading, time

ADAPTER, HOOK_MODE = sys.argv[1:3]
sys.argv = ["worker"]

from runpod.serverless.modules.rp_fastapi import WorkerAPI
from runpod.serverless.modules.rp_local import run_local
from runpod.serverless.modules.rp_prestart import register_prestart_hook
from runpod.serverless.modules.rp_scale import JobScaler


def sync_engine():
    # A sync hook runs on a daemon thread, so its children inherit daemon status.
    # An engine that explicitly sets daemon=False still blocks cooperative exit.
    threading.Thread(target=lambda: time.sleep(60), daemon=False).start()
    raise RuntimeError("engine start failed")


async def async_engine():
    # An async hook runs on the main event loop; child threads inherit non-daemon.
    threading.Thread(target=lambda: time.sleep(60)).start()
    raise RuntimeError("engine start failed")


async def no_jobs(*args, **kwargs):
    return []


hook = {"sync": sync_engine, "async": async_engine}[HOOK_MODE]
config = {
    "handler": lambda job: job,
    "rp_args": {"test_input": {"input": "test"}},
}

if ADAPTER == "queue":
    scaler = JobScaler(config)
    scaler.prestart_hooks = (hook,)
    scaler.jobs_fetcher = no_jobs
    scaler.stop_signals_fetcher = no_jobs
    scaler.prestart_claim_timeout = 1
    asyncio.run(scaler.run())
else:
    register_prestart_hook(hook)
    if ADAPTER == "local":
        asyncio.run(run_local(config))
    else:
        worker = object.__new__(WorkerAPI)
        worker.config = config

        async def run_hosted():
            async with worker._lifespan(None):
                pass

        asyncio.run(run_hosted())

print("run() returned without exiting")
"""


class TestPrestartFailureExitsProcess(unittest.TestCase):
    """Prestart failure hard-exits even when a non-daemon thread remains."""

    def _run_worker(
        self, adapter: str, hook_mode: str = "async"
    ) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            script = pathlib.Path(tmp) / "worker.py"
            script.write_text(_HARD_EXIT_SCRIPT)
            return subprocess.run(
                [sys.executable, str(script), adapter, hook_mode],
                capture_output=True,
                text=True,
                timeout=30,  # generous: the fix exits in well under a second
                check=False,
            )

    def assert_hard_exit(self, adapter: str, hook_mode: str = "async") -> None:
        result = self._run_worker(adapter, hook_mode)
        assert result.returncode == 1
        assert "run() returned without exiting" not in result.stdout
        assert "prestart_failed" in result.stdout + result.stderr

    def test_queue_sync_hook_leaving_a_non_daemon_thread(self):
        self.assert_hard_exit("queue", "sync")

    def test_queue_async_hook_leaving_a_non_daemon_thread(self):
        self.assert_hard_exit("queue")

    def test_local_hook_leaving_a_non_daemon_thread(self):
        self.assert_hard_exit("local")

    def test_hosted_api_hook_leaving_a_non_daemon_thread(self):
        self.assert_hard_exit("hosted")


if __name__ == "__main__":
    unittest.main()
