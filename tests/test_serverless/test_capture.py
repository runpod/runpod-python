"""Tests for stdout/stderr capture: what a failing handler printed is attached to the
error it reports back, and every reported field stays bounded."""

# pylint: disable=protected-access

import asyncio
import io
import json
import sys
import unittest
from unittest.mock import patch

from runpod.serverless.modules import rp_capture
from runpod.serverless.modules.rp_job import run_job, run_job_generator


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
    """Error strings shipped back to the platform are bounded so a huge message/log can't
    blow past the job-done body limit."""

    def test_clip_keeps_head_and_tail(self):
        text = "A" * 100 + "B" * 100
        clipped = rp_capture.clip(text, limit=40)
        assert clipped.startswith("A" * 20)
        assert clipped.endswith("B" * 20)
        assert "truncated" in clipped
        assert len(clipped) < len(text)

    def test_clip_passthrough_when_small(self):
        assert rp_capture.clip("short", limit=100) == "short"

    def test_run_job_bounds_error_message(self):
        huge = "x" * (rp_capture.MAX_CAPTURED_CHARS * 3)

        def handler(_job):
            raise ValueError(huge)

        result = _run(run_job(handler, {"id": "big"}))
        error = json.loads(result["error"])
        assert len(error["error_message"]) <= rp_capture.MAX_CAPTURED_CHARS + 100

    def test_run_job_generator_bounds_combined_error(self):
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

        assert len(result[0]["error"]) <= rp_capture.MAX_CAPTURED_CHARS + 100


if __name__ == "__main__":
    unittest.main()
