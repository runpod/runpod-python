"""Tests for the concurrent initializer: runs alongside the job loop, holds the handler
until ready, and fails the in-hand request (with captured stdout/stderr) on failure."""

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

from runpod.serverless.modules import rp_capture, rp_scale
from runpod.serverless.modules.rp_initializer import (
    InitializerError,
    InitializerTimeout,
    build_init_failed_payload,
    run_initializer_async,
)
from runpod.serverless.modules.rp_scale import JobScaler


def _run(coro):
    return asyncio.run(coro)


class TestRunInitializerAsync(unittest.TestCase):
    """Runs the initializer to completion, or raises a clear error when it fails or times out."""

    def test_sync_success_offloaded(self):
        calls = []
        _run(run_initializer_async(lambda: calls.append("ran")))
        assert calls == ["ran"]

    def test_sync_callable_returning_awaitable_is_awaited(self):
        state = {}

        async def load():
            await asyncio.sleep(0)
            state["ready"] = True

        def initialize():
            return load()

        _run(run_initializer_async(initialize))
        assert state == {"ready": True}


    def test_sync_failure_wraps_in_initializer_error(self):
        def failing_initializer():
            raise ValueError("max_model_len must be positive, got 0")

        with self.assertRaises(InitializerError) as ctx:
            _run(run_initializer_async(failing_initializer))
        assert isinstance(ctx.exception.original, ValueError)
        assert "max_model_len" in str(ctx.exception)

    def test_async_success(self):
        state = {}

        async def load():
            await asyncio.sleep(0)
            state["ready"] = True

        _run(run_initializer_async(load))
        assert state == {"ready": True}

    def test_async_failure_wraps_in_initializer_error(self):
        async def failing_initializer():
            raise RuntimeError("CUDA OOM")

        with self.assertRaises(InitializerError) as ctx:
            _run(run_initializer_async(failing_initializer))
        assert isinstance(ctx.exception.original, RuntimeError)

    def test_async_timeout_raises_initializer_timeout(self):
        async def slow():
            await asyncio.sleep(3)

        with self.assertRaises(InitializerTimeout):
            _run(run_initializer_async(slow, timeout=1))

    def test_zero_timeout_raises_initializer_timeout(self):
        async def load():
            await asyncio.sleep(0)

        with self.assertRaises(InitializerTimeout):
            _run(run_initializer_async(load, timeout=0))


    def test_sync_hang_times_out(self):
        """A blocking sync load that never returns is cut off by init_timeout, not left stuck."""
        release = threading.Event()

        def hang():
            release.wait(10)

        try:
            with self.assertRaises(InitializerTimeout):
                _run(run_initializer_async(hang, timeout=1))
        finally:
            release.set()  # let the offloaded thread exit promptly

    def test_async_partial_is_awaited(self):
        """functools.partial wrapping an async initializer is detected and awaited."""
        state = {}

        async def load(key):
            await asyncio.sleep(0)
            state[key] = True

        _run(run_initializer_async(functools.partial(load, "ready")))
        assert state == {"ready": True}

    def test_async_callable_object_is_awaited(self):
        """An object whose __call__ is async is detected and awaited."""
        state = {}

        class Loader:
            async def __call__(self):
                await asyncio.sleep(0)
                state["ready"] = True

        _run(run_initializer_async(Loader()))
        assert state == {"ready": True}

    def test_base_exception_propagates_unwrapped(self):
        """KeyboardInterrupt is process control, not an init failure: propagate."""

        def interrupted():
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            _run(run_initializer_async(interrupted))

    def test_system_exit_wraps_as_initializer_error(self):
        """A load script calling sys.exit() is an init failure to surface with a reason,
        not a clean exit - otherwise held in-hand jobs die without one."""

        def bails():
            raise SystemExit(1)

        with self.assertRaises(InitializerError):
            _run(run_initializer_async(bails))


class TestInitFailedSignal(unittest.TestCase):
    """Builds the structured init_failed payload, including captured logs."""

    def test_payload_shape_from_sync_error(self):
        try:
            raise ValueError("bad config")
        except ValueError as exc:
            payload = build_init_failed_payload(
                InitializerError(exc), logs="stderr tail"
            )
        assert payload["event"] == "init_failed"
        assert payload["error_type"] == "ValueError"
        assert payload["error_message"] == "bad config"
        assert "ValueError" in payload["error_traceback"]
        assert payload["logs"] == "stderr tail"
        assert "worker_id" in payload and "runpod_version" in payload

    def test_payload_omits_empty_logs(self):
        payload = build_init_failed_payload(InitializerError(ValueError("x")))
        assert "logs" not in payload

    def test_payload_bounds_message_traceback_and_logs(self):
        huge = "x" * (rp_capture.MAX_CAPTURED_CHARS * 3)
        payload = build_init_failed_payload(
            InitializerError(ValueError(huge)),
            logs="y" * (rp_capture.MAX_CAPTURED_CHARS * 3),
        )
        assert len(payload["error_message"]) <= rp_capture.MAX_CAPTURED_CHARS + 100
        assert len(payload["error_traceback"]) <= rp_capture.MAX_CAPTURED_CHARS + 100
        assert len(payload["logs"]) <= rp_capture.MAX_CAPTURED_CHARS


def _scaler(initializer=None):
    config = {"handler": lambda j: j, "rp_args": {}}
    if initializer is not None:
        config["initializer"] = initializer
    scaler = JobScaler(config)
    scaler.job_progress = MagicMock()  # avoid the process-wide singleton in unit tests
    scaler.job_progress.get_job_count.return_value = 0
    return scaler


class TestRunInit(unittest.TestCase):
    """The concurrent init task: opens the gate, and on failure records the reason and shuts down."""

    def test_no_initializer_opens_gate_immediately(self):
        scaler = _scaler(initializer=None)
        _run(scaler._run_init())
        assert scaler._init_ready.is_set()
        assert scaler._init_error is None

    def test_success_opens_gate_no_error(self):
        ran = []
        scaler = _scaler(initializer=lambda: ran.append(1))
        _run(scaler._run_init())
        assert ran == [1]
        assert scaler._init_ready.is_set()
        assert scaler._init_error is None
        assert not scaler._shutdown_event.is_set()

    def test_failure_records_reason_with_logs_and_shuts_down(self):
        def failing_initializer():
            print("downloading model")
            raise RuntimeError("CUDA OOM: model too big")

        scaler = _scaler(initializer=failing_initializer)
        real = io.StringIO()
        with (
            patch.object(sys, "stdout", rp_capture._TeeProxy(real)),
            patch.object(rp_scale, "log") as mock_log,
        ):
            _run(scaler._run_init())

        assert scaler._init_ready.is_set()  # held handlers are released to fail fast
        assert scaler._init_error is not None
        assert scaler._init_error["error_message"] == "CUDA OOM: model too big"
        assert "downloading model" in scaler._init_error["logs"]
        assert any(
            call.args[0].startswith("init_failed | ")
            for call in mock_log.error.call_args_list
        )
        assert (
            scaler._shutdown_event.is_set()
        )  # broken worker shuts down (occupancy was 0)

    def test_failure_starts_shutdown_before_drain(self):
        scaler = _scaler(
            initializer=lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        shutdown_states = []

        def occupancy():
            shutdown_states.append(scaler._shutdown_event.is_set())
            return 0

        scaler.current_occupancy = occupancy
        _run(scaler._run_init())

        assert shutdown_states == [True]


class TestHandleJobGate(unittest.TestCase):
    """The handler is held until init is ready; init failure fails the in-hand request."""

    def _prime(self, scaler, job):
        # Balance the queue/progress bookkeeping handle_job's finally expects.
        scaler.jobs_queue = asyncio.Queue(maxsize=4)
        scaler.jobs_queue.put_nowait(job)

    def test_runs_handler_once_init_is_ready(self):
        scaler = _scaler(initializer=lambda: None)
        scaler.jobs_handler = AsyncMock()
        scaler._init_ready.set()  # init already succeeded
        job = {"id": "j1"}

        async def go():
            self._prime(scaler, job)
            await scaler.handle_job(None, job)

        _run(go())
        scaler.jobs_handler.assert_awaited_once()

    def test_runs_handler_without_an_initializer(self):
        scaler = _scaler(initializer=None)
        scaler.jobs_handler = AsyncMock()
        job = {"id": "j2"}

        async def go():
            self._prime(scaler, job)
            await scaler.handle_job(None, job)

        _run(go())
        scaler.jobs_handler.assert_awaited_once()  # no gate to wait on

    def test_init_failure_fails_request_without_running_handler(self):
        scaler = _scaler(initializer=lambda: None)
        scaler.jobs_handler = AsyncMock()
        scaler._init_error = {"error_message": "CUDA OOM", "event": "init_failed"}
        scaler._init_ready.set()
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

    def test_init_failure_before_any_take_claims_a_request_to_fail(self):
        """An instant init failure leaves no request in hand, and it sets shutdown, so
        job-take ends immediately. It must still claim one request and fail it, or the
        caller waits out the queue TTL for nothing."""
        scaler = _scaler(initializer=lambda: None)
        scaler._init_error = {"error_message": "CUDA OOM", "event": "init_failed"}
        scaler.kill_worker()  # _run_init sets shutdown on failure
        scaler._fail_job = AsyncMock()
        job = {"id": "orphan-1"}
        scaler.jobs_fetcher = AsyncMock(return_value=[job])

        _run(asyncio.wait_for(scaler.get_jobs(AsyncMock()), timeout=0.5))

        scaler.jobs_fetcher.assert_awaited_once()
        scaler._fail_job.assert_awaited_once()
        assert scaler._fail_job.await_args[0][1] is job
        assert scaler.jobs_queue.qsize() == 0  # never queued into a broken worker

    def test_init_failure_with_empty_queue_exits_without_hanging(self):
        """Nothing left to fail: the claim attempt returns empty and job-take stops."""
        scaler = _scaler(initializer=lambda: None)
        scaler._init_error = {"error_message": "CUDA OOM", "event": "init_failed"}
        scaler.kill_worker()
        scaler._fail_job = AsyncMock()
        scaler.jobs_fetcher = AsyncMock(return_value=[])

        _run(asyncio.wait_for(scaler.get_jobs(AsyncMock()), timeout=0.5))

        scaler.jobs_fetcher.assert_awaited_once()
        scaler._fail_job.assert_not_awaited()

    def test_init_failure_after_a_take_does_not_claim_another_request(self):
        """Once this worker has claimed a request, the failure is reported against it.
        Claiming a second request would fail work a healthy worker could serve."""
        scaler = _scaler(initializer=lambda: None)
        scaler._fail_job = AsyncMock()
        failure = {"error_message": "CUDA OOM", "event": "init_failed"}
        calls = []

        async def fetcher(_session, _needed):
            calls.append(1)
            return [{"id": f"job-{len(calls)}"}]

        scaler.jobs_fetcher = fetcher

        async def go():
            task = asyncio.create_task(scaler.get_jobs(AsyncMock()))
            await asyncio.sleep(0)
            scaler._init_error = failure  # lands after the first take succeeded
            scaler.kill_worker()
            await asyncio.wait_for(task, timeout=0.5)

        _run(go())

        assert len(calls) == 1  # no extra claim after the queued request

    def test_instant_async_init_failure_still_fails_the_queued_request(self):
        """An async initializer that raises before its first await finishes before
        job-take ever fetches, so nothing is in hand and shutdown is already set.
        Driving the real `_run_init` failure path, the worker must still claim the
        queued request and fail it rather than exit silently."""

        async def bad_init():
            raise RuntimeError("bad config")

        scaler = _scaler(initializer=bad_init)
        scaler._fail_job = AsyncMock()
        job = {"id": "queued-1"}
        scaler.jobs_fetcher = AsyncMock(return_value=[job])
        scaler.jobs_handler = AsyncMock()

        async def go():
            await scaler._run_init()  # records the reason and sets shutdown
            assert not scaler.is_alive()
            await asyncio.wait_for(scaler.get_jobs(AsyncMock()), timeout=1)

        _run(go())

        scaler._fail_job.assert_awaited_once()
        assert scaler._fail_job.await_args[0][1] is job
        reason = scaler._fail_job.await_args[0][2]
        assert reason["event"] == "init_failed"
        assert reason["error_message"] == "bad config"
        scaler.jobs_handler.assert_not_awaited()  # handler never runs on a broken worker

    def test_claim_attempt_is_bounded(self):
        """A silent job-take must not hold a dying worker open. The claim gives up on
        its own bound and the worker exits to be respawned."""
        scaler = _scaler(initializer=lambda: None)
        scaler._init_error = {"error_message": "CUDA OOM", "event": "init_failed"}
        scaler.kill_worker()
        scaler._fail_job = AsyncMock()
        scaler.init_claim_timeout = 0.05

        async def never_returns(_session, _needed):
            await asyncio.Event().wait()

        scaler.jobs_fetcher = never_returns

        _run(asyncio.wait_for(scaler.get_jobs(AsyncMock()), timeout=1))

        scaler._fail_job.assert_not_awaited()

    def test_jobs_acquired_after_init_failure_are_failed_not_queued(self):
        """If init fails while a long-poll is in flight, fail returned jobs and stop
        job-take without relying on another task to end the loop."""
        scaler = _scaler(initializer=lambda: None)
        scaler._fail_job = AsyncMock()
        job = {"id": "late-1"}
        failure = {"error_message": "CUDA OOM", "event": "init_failed"}

        async def fetcher(_session, _needed):
            # Init failure lands while this long-poll is in flight.
            scaler._init_error = failure
            return [job]

        scaler.jobs_fetcher = fetcher
        _run(asyncio.wait_for(scaler.get_jobs(AsyncMock()), timeout=0.5))
        scaler._fail_job.assert_awaited_once()
        assert scaler._fail_job.await_args[0][1] is job
        assert scaler.jobs_queue.qsize() == 0
        scaler.job_progress.add.assert_not_called()

    def test_shutdown_before_init_ready_leaves_request_alone(self):
        """SIGTERM while the initializer is still running must release a held request
        without running the handler against an uninitialized worker."""
        scaler = _scaler(initializer=lambda: None)
        scaler.jobs_handler = AsyncMock()
        scaler.kill_worker()  # shutdown lands while init is still in flight
        job = {"id": "j4"}

        async def go():
            self._prime(scaler, job)
            with patch.object(rp_scale, "send_result", new=AsyncMock()) as mock_sr:
                await asyncio.wait_for(scaler.handle_job(None, job), timeout=0.5)
                mock_sr.assert_not_awaited()  # the platform retries it elsewhere

        _run(go())
        scaler.jobs_handler.assert_not_awaited()


class TestShutdownWithHangingInit(unittest.TestCase):
    """A hung initializer with no init_timeout must not outlive shutdown: the daemon
    thread is abandoned so the worker can exit."""

    def test_run_returns_while_initializer_still_blocked(self):
        release = threading.Event()
        self.addCleanup(release.set)  # let the daemon thread finish after the test

        scaler = _scaler(initializer=release.wait)  # blocks with no init_timeout
        scaler.kill_worker()  # every loop exits immediately; only init is left
        scaler.stop_signals_fetcher = AsyncMock(return_value=[])

        _run(asyncio.wait_for(scaler.run(), timeout=2))

        assert not release.is_set()  # still blocked, and the worker left anyway


# Needs a real process: in-process the interpreter never joins threads, so the hang
# this guards against cannot be observed.
_HARD_EXIT_SCRIPT = """
import asyncio, sys, threading, time

MODE = sys.argv[1]
sys.argv = ["worker"]

from runpod.serverless.modules.rp_scale import JobScaler


def sync_engine():
    # A sync initializer runs on a daemon thread, so its children inherit daemon
    # status. An engine that sets daemon=False itself does not.
    threading.Thread(target=lambda: time.sleep(60), daemon=False).start()
    raise RuntimeError("engine start failed")


async def async_engine():
    # An async initializer is awaited on the main thread, so anything it spawns is
    # non-daemon by inheritance.
    threading.Thread(target=lambda: time.sleep(60)).start()
    raise RuntimeError("engine start failed")


async def no_jobs(*args, **kwargs):
    return []


scaler = JobScaler(
    {
        "handler": lambda job: job,
        "rp_args": {},
        "initializer": {"sync": sync_engine, "async": async_engine}[MODE],
    }
)
scaler.jobs_fetcher = no_jobs
scaler.stop_signals_fetcher = no_jobs
scaler.init_claim_timeout = 1

asyncio.run(scaler.run())
print("run() returned without exiting")
"""


class TestInitFailureExitsProcess(unittest.TestCase):
    """Init failure must hard-exit, even with a non-daemon thread left running."""

    def _run_worker(self, mode: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            script = pathlib.Path(tmp) / "worker.py"
            script.write_text(_HARD_EXIT_SCRIPT)
            return subprocess.run(
                [sys.executable, str(script), mode],
                capture_output=True,
                text=True,
                timeout=30,  # generous: the fix exits in well under a second
                check=False,
            )

    def test_sync_initializer_leaving_a_non_daemon_thread(self):
        result = self._run_worker("sync")
        assert result.returncode == 1
        assert "run() returned without exiting" not in result.stdout

    def test_async_initializer_leaving_a_non_daemon_thread(self):
        result = self._run_worker("async")
        assert result.returncode == 1
        assert "init_failed" in result.stdout + result.stderr  # reported before exiting


if __name__ == "__main__":
    unittest.main()
