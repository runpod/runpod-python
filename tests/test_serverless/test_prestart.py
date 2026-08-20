"""Public prestart-hook contract for queue-based Serverless workers."""

import asyncio
import os
import unittest
from unittest.mock import patch

import runpod.serverless
from runpod.serverless.modules import rp_fastapi, rp_prestart
from runpod.serverless.modules.rp_prestart import (
    PrestartError,
    PrestartTimeout,
    build_prestart_failed_payload,
    clear_prestart_hooks,
    get_prestart_hooks,
    run_prestart_hooks_async,
)


def _run(coro):
    return asyncio.run(coro)


class TestPrestartRegistry(unittest.TestCase):
    def setUp(self):
        clear_prestart_hooks()

    def tearDown(self):
        clear_prestart_hooks()

    def test_decorator_registers_hooks_in_order_and_returns_each_callable(self):
        def first():
            return None

        async def second():
            return None

        assert runpod.serverless.register_prestart_hook(first) is first
        assert runpod.serverless.register_prestart_hook(second) is second
        assert get_prestart_hooks() == (first, second)

    def test_mixed_hooks_run_sequentially(self):
        calls = []

        @runpod.serverless.register_prestart_hook
        def first():
            calls.append("first")

        @runpod.serverless.register_prestart_hook
        async def second():
            calls.append("second")
            await asyncio.sleep(0)
            calls.append("second-ready")

        @runpod.serverless.register_prestart_hook
        def third():
            calls.append("third")

        _run(run_prestart_hooks_async(get_prestart_hooks()))

        assert calls == ["first", "second", "second-ready", "third"]

    def test_failure_stops_later_hooks_and_names_the_failing_hook(self):
        calls = []

        def load_model():
            calls.append("load_model")
            raise RuntimeError("CUDA OOM")

        def warm_cache():
            calls.append("warm_cache")

        with self.assertRaises(PrestartError) as ctx:
            _run(run_prestart_hooks_async((load_model, warm_cache)))

        assert calls == ["load_model"]
        assert ctx.exception.hook == "load_model"
        payload = build_prestart_failed_payload(ctx.exception)
        assert payload["event"] == "prestart_failed"
        assert payload["hook"] == "load_model"
        assert payload["error_message"] == "CUDA OOM"

    def test_timeout_bounds_the_whole_phase_and_names_current_hook(self):
        started = []

        async def first():
            started.append("first")

        async def second():
            started.append("second")
            # Blocks forever, so the deadline lands here on any scheduler.
            await asyncio.Event().wait()

        with self.assertRaises(PrestartTimeout) as ctx:
            _run(run_prestart_hooks_async((first, second), timeout=0.05))

        assert started == ["first", "second"]
        assert ctx.exception.hook == "second"

    def test_hook_raised_cancelled_error_is_a_prestart_failure(self):
        async def cancelled_hook():
            raise asyncio.CancelledError

        with self.assertRaises(PrestartError) as ctx:
            _run(run_prestart_hooks_async((cancelled_hook,)))

        assert isinstance(ctx.exception.original, asyncio.CancelledError)
        assert ctx.exception.hook == "cancelled_hook"

    def test_external_task_cancellation_remains_cancellation(self):
        async def scenario():
            started = asyncio.Event()

            async def blocked_hook():
                started.set()
                await asyncio.Event().wait()

            task = asyncio.create_task(run_prestart_hooks_async((blocked_hook,)))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        _run(scenario())


class TestHostedAPIPrestart(unittest.TestCase):
    def setUp(self):
        clear_prestart_hooks()

    def tearDown(self):
        clear_prestart_hooks()

    def test_lifespan_runs_hooks_before_serving(self):
        state = []

        @runpod.serverless.register_prestart_hook
        async def load_model():
            state.append("ready")

        async def scenario():
            with patch.object(rp_fastapi.heartbeat, "start_ping"):
                api = rp_fastapi.WorkerAPI(
                    {"handler": lambda job: job, "prestart_timeout": 1}
                )
            async with api.rp_app.router.lifespan_context(api.rp_app):
                self.assertEqual(state, ["ready"])

        _run(scenario())

    def test_lifespan_failure_prevents_serving(self):
        @runpod.serverless.register_prestart_hook
        def load_model():
            raise RuntimeError("model unavailable")

        async def scenario():
            with patch.object(rp_fastapi.heartbeat, "start_ping"):
                api = rp_fastapi.WorkerAPI({"handler": lambda job: job})
            with (
                patch.object(rp_prestart.log, "error") as logger,
                # The real helper calls os._exit; SystemExit stands in for that.
                patch.object(
                    rp_fastapi, "_terminate_unhealthy", side_effect=SystemExit(1)
                ) as terminate,
                self.assertRaises(SystemExit),
            ):
                async with api.rp_app.router.lifespan_context(api.rp_app):
                    self.fail("API served despite prestart failure")
            terminate.assert_called_once_with(1)
            self.assertIn("prestart_failed", logger.call_args.args[0])
            self.assertIn("load_model", logger.call_args.args[0])

        _run(scenario())


class TestPrestartModeGuard(unittest.TestCase):
    def setUp(self):
        clear_prestart_hooks()

        @runpod.serverless.register_prestart_hook
        def load_model():
            return None

    def tearDown(self):
        clear_prestart_hooks()

    @staticmethod
    def _config(*, serve_api=False):
        return {
            "handler": lambda job: job,
            "rp_args": {
                "rp_log_level": None,
                "rp_debugger": None,
                "rp_serve_api": serve_api,
                "rp_api_port": 8000,
                "rp_api_concurrency": 1,
                "rp_api_host": "localhost",
                "test_input": None,
            },
        }

    def test_hosted_api_accepts_registered_hooks(self):
        config = self._config(serve_api=True)
        with (
            patch("runpod.serverless._set_config_args", return_value=config),
            patch("runpod.serverless.signal.signal"),
            patch("runpod.serverless.modules.rp_fastapi.WorkerAPI") as worker_api,
        ):
            runpod.serverless.start(config)

        worker_api.assert_called_once_with(config)
        worker_api.return_value.start_uvicorn.assert_called_once()

    def test_hosted_api_precedes_realtime_environment(self):
        config = self._config(serve_api=True)
        with (
            patch("runpod.serverless._set_config_args", return_value=config),
            patch("runpod.serverless.signal.signal"),
            patch.dict(os.environ, {"RUNPOD_REALTIME_PORT": "8000"}),
            patch("runpod.serverless.modules.rp_fastapi.WorkerAPI") as worker_api,
        ):
            runpod.serverless.start(config)

        worker_api.assert_called_once_with(config)

    def test_hosted_api_rejects_multiple_workers(self):
        config = self._config(serve_api=True)
        config["rp_args"]["rp_api_concurrency"] = 2
        with (
            patch("runpod.serverless._set_config_args", return_value=config),
            patch("runpod.serverless.signal.signal"),
            self.assertRaisesRegex(RuntimeError, "rp_api_concurrency=1"),
        ):
            runpod.serverless.start(config)

    def test_realtime_rejects_registered_hooks(self):
        config = self._config()
        with (
            patch("runpod.serverless._set_config_args", return_value=config),
            patch("runpod.serverless.signal.signal"),
            patch.dict(os.environ, {"RUNPOD_REALTIME_PORT": "8000"}),
            self.assertRaisesRegex(RuntimeError, "realtime mode"),
        ):
            runpod.serverless.start(config)

    def test_local_accepts_registered_hooks(self):
        config = self._config()
        with (
            patch("runpod.serverless._set_config_args", return_value=config),
            patch("runpod.serverless.signal.signal"),
            patch("runpod.serverless.worker.main") as worker_main,
            patch.dict(os.environ, {}, clear=True),
        ):
            runpod.serverless.start(config)

        worker_main.assert_called_once_with(config)

    def test_queue_worker_accepts_registered_hooks(self):
        config = self._config()
        with (
            patch("runpod.serverless._set_config_args", return_value=config),
            patch("runpod.serverless.signal.signal"),
            patch("runpod.serverless.worker.main") as worker_main,
            patch.dict(
                os.environ,
                {"RUNPOD_WEBHOOK_GET_JOB": "https://api.runpod.ai/job-take"},
                clear=True,
            ),
        ):
            runpod.serverless.start(config)

        worker_main.assert_called_once_with(config)


if __name__ == "__main__":
    unittest.main()
