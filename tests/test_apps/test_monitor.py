"""worker monitor: metrics transitions, log attach, event emission."""

import asyncio
from unittest.mock import patch

import pytest

from runpod.apps.monitor import WorkerMonitor, emit, format_worker_counts


class Sink:
    def __init__(self):
        self.events = []

    def worker_status(self, name, counts):
        self.events.append(("worker_status", name, counts))

    def worker_ready(self, name, worker_id):
        self.events.append(("worker_ready", name, worker_id))

    def worker_log(self, name, line):
        self.events.append(("worker_log", name, line))


class TestEmit:
    def test_missing_handler_skipped(self):
        emit(object(), "nope", 1)

    def test_none_sink_skipped(self):
        emit(None, "anything")

    def test_handler_called(self):
        sink = Sink()
        emit(sink, "worker_ready", "calc", "w1")
        assert sink.events == [("worker_ready", "calc", "w1")]

    def test_handler_errors_swallowed(self):
        class Bad:
            def worker_ready(self, *a):
                raise RuntimeError("render bug")

        emit(Bad(), "worker_ready", "calc", "w1")


class TestFormatWorkerCounts:
    def test_only_nonzero(self):
        s = format_worker_counts({"initializing": 1, "ready": 2, "throttled": 0})
        assert s == "1 initializing · 2 ready"

    def test_empty(self):
        assert format_worker_counts({"ready": 0}) == "no workers"


class TestOnStatus:
    def test_worker_id_triggers_ready_and_stream(self):
        sink = Sink()
        monitor = WorkerMonitor("ep1", "calc", sink)

        async def run():
            with patch.object(
                monitor, "_stream_logs", return_value=asyncio.sleep(0)
            ):
                monitor.on_status({"workerId": "w123", "status": "IN_PROGRESS"})
                monitor.on_status({"workerId": "w123", "status": "IN_PROGRESS"})
                await monitor.stop()

        asyncio.run(run())
        assert sink.events == [("worker_ready", "calc", "w123")]

    def test_no_worker_id_no_events(self):
        sink = Sink()
        monitor = WorkerMonitor("ep1", "calc", sink)
        monitor.on_status({"status": "IN_QUEUE"})
        assert sink.events == []


class TestReportCounts:
    def test_first_steady_snapshot_suppressed(self):
        sink = Sink()
        monitor = WorkerMonitor("ep1", "calc", sink)
        monitor._report_counts({"workers": {"ready": 2, "running": 1}})
        assert sink.events == []

    def test_first_snapshot_with_initializing_reported(self):
        sink = Sink()
        monitor = WorkerMonitor("ep1", "calc", sink)
        monitor._report_counts({"workers": {"initializing": 1}})
        assert len(sink.events) == 1
        assert sink.events[0][2]["initializing"] == 1

    def test_transition_reported_once(self):
        sink = Sink()
        monitor = WorkerMonitor("ep1", "calc", sink)
        monitor._report_counts({"workers": {"initializing": 1}})
        monitor._report_counts({"workers": {"initializing": 1}})
        monitor._report_counts({"workers": {"ready": 1}})
        assert len(sink.events) == 2
        assert sink.events[1][2]["ready"] == 1

    def test_malformed_payload_ignored(self):
        sink = Sink()
        monitor = WorkerMonitor("ep1", "calc", sink)
        monitor._report_counts({"workers": None})
        monitor._report_counts({})
        assert sink.events == []
