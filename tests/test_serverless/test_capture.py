"""Tests for stdout/stderr capture: what a failing handler printed is attached to the
error it reports back, and every reported field stays bounded."""

# pylint: disable=protected-access

import asyncio
import io
import json
import os
import sys
import unittest
from unittest.mock import patch

from runpod.serverless.modules import rp_capture
from runpod.serverless.modules.rp_job import run_job, run_job_generator
from runpod.serverless.modules.rp_prestart import (
    clear_prestart_hooks,
    register_prestart_hook,
)


def _run(coro):
    return asyncio.run(coro)


def _run_gen(agen):
    async def _drain():
        return [item async for item in agen]

    return asyncio.run(_drain())


class TestStdioCapture(unittest.TestCase):
    """Per-context stdout/stderr capture: records into the active buffer, passes through."""

    def test_records_and_passes_through(self):
        real = io.StringIO()
        proxy = rp_capture._TeeProxy(real)
        with patch.object(sys, "stdout", proxy), rp_capture.capture() as cap:
            print("hello from handler")
        assert "hello from handler" in cap.getvalue()
        assert "hello from handler" in real.getvalue()  # still reached the real stream

    def test_no_capture_outside_block(self):
        real = io.StringIO()
        proxy = rp_capture._TeeProxy(real)
        with patch.object(sys, "stdout", proxy):
            with rp_capture.capture() as cap:
                pass
            print("after the block")
        assert "after the block" not in cap.getvalue()

    def test_ring_buffer_keeps_tail(self):
        buf = rp_capture._RingBuffer(limit=10)
        buf.write("0123456789ABCDEF")
        assert buf.getvalue() == "6789ABCDEF"

    def test_install_is_idempotent(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr):
            rp_capture.install()
            installed_stdout = sys.stdout
            installed_stderr = sys.stderr

            rp_capture.install()

            assert sys.stdout is installed_stdout
            assert sys.stderr is installed_stderr


class TestCaptureIsOptIn(unittest.TestCase):
    """A worker that has not adopted prestart hooks must see no change at all:
    streams untouched and no logs field in the failure it reports."""

    def setUp(self):
        clear_prestart_hooks()
        self.addCleanup(clear_prestart_hooks)

    def _install_with(self, env) -> bool:
        """Install under `env` and report whether the proxy was applied."""
        real_stdout, real_stderr = sys.__stdout__, sys.__stderr__
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(sys, "stdout", real_stdout),
            patch.object(sys, "stderr", real_stderr),
        ):
            rp_capture.install()
            return isinstance(sys.stdout, rp_capture._TeeProxy) and isinstance(
                sys.stderr, rp_capture._TeeProxy
            )

    def test_auto_skips_install_without_hooks(self):
        assert self._install_with({"RUNPOD_LOG_CAPTURE": "auto"}) is False

    def test_auto_is_the_default(self):
        env = {k: v for k, v in os.environ.items() if k != "RUNPOD_LOG_CAPTURE"}
        with patch.dict(os.environ, env, clear=True):
            assert rp_capture.capture_mode() == rp_capture.CAPTURE_AUTO
        assert self._install_with({}) is False

    def test_auto_installs_once_a_hook_is_registered(self):
        register_prestart_hook(lambda: None)
        assert self._install_with({"RUNPOD_LOG_CAPTURE": "auto"}) is True

    def test_all_installs_without_hooks(self):
        assert self._install_with({"RUNPOD_LOG_CAPTURE": "all"}) is True

    def test_off_skips_install_even_with_hooks(self):
        register_prestart_hook(lambda: None)
        assert self._install_with({"RUNPOD_LOG_CAPTURE": "off"}) is False

    def test_unknown_mode_falls_back_to_auto(self):
        with patch.dict(os.environ, {"RUNPOD_LOG_CAPTURE": "yes-please"}):
            assert rp_capture.capture_mode() == rp_capture.CAPTURE_AUTO

    def test_error_payload_is_unchanged_without_capture(self):
        def handler(_job):
            print("HF_TOKEN=hf_secret")
            raise RuntimeError("kernel panic")

        with patch.dict(os.environ, {"RUNPOD_LOG_CAPTURE": "off"}):
            rp_capture.install()
            result = _run(run_job(handler, {"id": "j3"}))

        error = json.loads(result["error"])
        assert error["error_message"] == "kernel panic"
        assert "logs" not in error


class TestRunJobCapturesLogs(unittest.TestCase):
    """A failing handler's stdout/stderr is attached to the job error output."""

    def test_handler_error_attaches_logs(self):
        def handler(_job):
            print("loading weights")
            print("boom trace", file=sys.stderr)
            raise RuntimeError("kernel panic")

        real = io.StringIO()
        with (
            patch.object(sys, "stdout", rp_capture._TeeProxy(real)),
            patch.object(sys, "stderr", rp_capture._TeeProxy(real)),
        ):
            result = _run(run_job(handler, {"id": "j1"}))

        error = json.loads(result["error"])
        assert error["error_message"] == "kernel panic"
        assert "loading weights" in error["logs"]
        assert "boom trace" in error["logs"]

    def test_generator_error_attaches_logs_in_error(self):
        def handler(_job):
            print("loading weights")
            print("boom trace", file=sys.stderr)
            raise RuntimeError("kernel panic")
            yield  # pragma: no cover - makes handler a generator

        real = io.StringIO()
        with (
            patch.object(sys, "stdout", rp_capture._TeeProxy(real)),
            patch.object(sys, "stderr", rp_capture._TeeProxy(real)),
        ):
            result = _run_gen(run_job_generator(handler, {"id": "g1"}))

        assert len(result) == 1
        assert set(result[0]) == {"error"}
        assert "kernel panic" in result[0]["error"]
        assert "loading weights" in result[0]["error"]
        assert "boom trace" in result[0]["error"]

    def test_handler_success_has_no_error(self):
        result = _run(run_job(lambda _job: {"ok": True}, {"id": "j2"}))
        assert result == {"output": {"ok": True}}


class TestErrorFieldBounding(unittest.TestCase):
    """Only the log tail this SDK adds is bounded. A handler's own error message and
    traceback are reported exactly as before, so existing workers see no truncation."""

    def test_clip_keeps_head_and_tail(self):
        text = "A" * 100 + "B" * 100
        clipped = rp_capture.clip(text, limit=40)
        assert clipped.startswith("A" * 20)
        assert clipped.endswith("B" * 20)
        assert "truncated" in clipped
        assert len(clipped) < len(text)

    def test_clip_passthrough_when_small(self):
        assert rp_capture.clip("short", limit=100) == "short"

    def test_run_job_reports_a_huge_handler_error_untruncated(self):
        huge = "x" * (rp_capture.MAX_CAPTURED_CHARS * 3)

        def handler(_job):
            raise ValueError(huge)

        result = _run(run_job(handler, {"id": "big"}))
        error = json.loads(result["error"])
        assert error["error_message"] == huge
        assert "truncated" not in error["error_traceback"]

    def test_run_job_bounds_only_the_captured_logs(self):
        def handler(_job):
            print("y" * (rp_capture.MAX_CAPTURED_CHARS * 3))
            raise ValueError("boom")

        real = io.StringIO()
        with (
            patch.object(sys, "stdout", rp_capture._TeeProxy(real)),
            patch.object(sys, "stderr", rp_capture._TeeProxy(real)),
        ):
            result = _run(run_job(handler, {"id": "big-logs"}))

        error = json.loads(result["error"])
        assert error["error_message"] == "boom"
        assert len(error["logs"]) <= rp_capture.MAX_CAPTURED_CHARS

    def test_run_job_generator_bounds_only_the_captured_logs(self):
        huge = "x" * (rp_capture.MAX_CAPTURED_CHARS * 3)

        def handler(_job):
            print("y" * (rp_capture.MAX_CAPTURED_CHARS * 3))
            raise ValueError(huge)
            yield  # pragma: no cover - makes handler a generator

        real = io.StringIO()
        with (
            patch.object(sys, "stdout", rp_capture._TeeProxy(real)),
            patch.object(sys, "stderr", rp_capture._TeeProxy(real)),
        ):
            result = _run_gen(run_job_generator(handler, {"id": "big-generator"}))

        # The handler's own error survives whole; only the appended log tail is capped.
        assert huge in result[0]["error"]
        logs = result[0]["error"].split("\nlogs:\n", 1)[1]
        assert len(logs) <= rp_capture.MAX_CAPTURED_CHARS

    def test_proxy_is_indistinguishable_from_a_text_stream(self):
        proxy = rp_capture._TeeProxy(io.StringIO())
        assert isinstance(proxy, io.TextIOBase)
        assert isinstance(proxy, io.IOBase)

    def test_writelines_is_captured(self):
        real = io.StringIO()
        proxy = rp_capture._TeeProxy(real)
        with patch.object(sys, "stdout", proxy), rp_capture.capture() as buf:
            sys.stdout.writelines(["a\n", "b\n"])
        assert buf.getvalue() == "a\nb\n"
        assert real.getvalue() == "a\nb\n"


if __name__ == "__main__":
    unittest.main()
