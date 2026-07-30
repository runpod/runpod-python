"""worker monitor: metrics transitions, log attach, event emission."""

import asyncio
from unittest.mock import AsyncMock, patch

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
        assert s == "1 initializing, 2 ready"

    def test_empty(self):
        assert format_worker_counts({"ready": 0}) == "no workers"


class TestOnStatus:
    def test_worker_id_triggers_ready_and_stream(self):
        sink = Sink()
        monitor = WorkerMonitor("ep1", "calc", sink)

        async def run():
            with patch(
                "runpod.apps.monitor.PodLogStream.attach"
            ) as attach, patch(
                "runpod.apps.monitor.PodLogStream.stop",
                return_value=asyncio.sleep(0),
            ):
                monitor.on_status({"workerId": "w123", "status": "IN_PROGRESS"})
                monitor.on_status({"workerId": "w123", "status": "IN_PROGRESS"})
                # one stream per worker, not per status payload
                assert attach.call_count == 1
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


class TestLineFiltering:
    def test_user_prints_pass_through(self):
        from runpod.apps.monitor import _filter_line

        assert _filter_line("hello world") == "hello world"
        assert _filter_line("42") == "42"

    def test_sdk_info_frames_hidden(self):
        from runpod.apps.monitor import _filter_line

        assert (
            _filter_line('{"requestId": null, "message": "Jobs in queue: 1", "level": "INFO"}')
            is None
        )
        assert (
            _filter_line('{"requestId": "abc", "message": "Started.", "level": "INFO"}')
            is None
        )

    def test_sdk_error_frames_surface_message(self):
        from runpod.apps.monitor import _filter_line

        assert (
            _filter_line('{"requestId": "abc", "message": "boom", "level": "ERROR"}')
            == "boom"
        )

    def test_non_frame_json_passes_through(self):
        from runpod.apps.monitor import _filter_line

        # user code printing json without the sdk frame shape
        assert _filter_line('{"result": 5}') == '{"result": 5}'


class TestPodLogStream:
    async def test_follow_emits_and_dedups(self):
        from runpod.apps.monitor import PodLogStream

        sink = Sink()
        events = [
            {"ts": "t1", "line": "hello"},
            {"ts": "t1", "line": "hello"},  # duplicate frame
            {"ts": "t2", "line": ""},  # blank filtered
            {"ts": "t3", "line": "world"},
        ]

        calls = {"n": 0}

        async def fake_stream(pod_id, **kwargs):
            if calls["n"]:
                # second attach: park forever so stop() cancels us
                await asyncio.sleep(3600)
            calls["n"] += 1
            for event in events:
                yield event

        stream = PodLogStream("pod1", "calc", sink)
        with patch("runpod.apps.logs.stream_pod_logs", fake_stream):
            stream.attach()
            await asyncio.sleep(0.05)
            await stream.stop()

        logs = [e for e in sink.events if e[0] == "worker_log"]
        assert logs == [
            ("worker_log", "calc", "hello"),
            ("worker_log", "calc", "world"),
        ]

    async def test_stop_snapshots_when_stream_never_attached(self):
        from datetime import datetime, timedelta, timezone

        from runpod.apps.monitor import PodLogStream

        sink = Sink()

        async def failing_stream(pod_id, **kwargs):
            raise RuntimeError("403")
            yield  # pragma: no cover

        stream = PodLogStream("pod1", "calc", sink)
        after = (
            datetime.now(timezone.utc) + timedelta(seconds=5)
        ).isoformat()
        snapshot = {
            "container": [f"{after} recovered output"],
            "system": [],
        }
        with (
            patch("runpod.apps.logs.stream_pod_logs", failing_stream),
            patch(
                "runpod.apps.logs.pod_logs",
                AsyncMock(return_value=snapshot),
            ),
        ):
            stream.attach()
            await asyncio.sleep(0.05)
            await stream.stop()

        logs = [e for e in sink.events if e[0] == "worker_log"]
        assert logs == [("worker_log", "calc", "recovered output")]

    async def test_snapshot_skips_lines_before_since(self):
        from runpod.apps.monitor import PodLogStream

        sink = Sink()
        stale = "2000-01-01T00:00:00.000000000Z old line"
        snapshot = {"container": [stale], "system": []}
        stream = PodLogStream("pod1", "calc", sink)
        with patch(
            "runpod.apps.logs.pod_logs",
            AsyncMock(return_value=snapshot),
        ):
            await stream._snapshot()

        assert sink.events == []

    async def test_attach_idempotent(self):
        from runpod.apps.monitor import PodLogStream

        async def parked(pod_id, **kwargs):
            await asyncio.sleep(3600)
            yield  # pragma: no cover

        stream = PodLogStream("pod1", "calc", Sink())
        with patch("runpod.apps.logs.stream_pod_logs", parked):
            stream.attach()
            first = stream._task
            stream.attach()
            assert stream._task is first
            stream._lines_emitted = 1  # skip the snapshot fallback
            await stream.stop()
        assert stream._task is None


class TestMonitorLifecycle:
    async def test_start_without_metrics_key_is_noop(self):
        monitor = WorkerMonitor("ep1", "calc", Sink())
        await monitor.start()
        assert monitor._tasks == []
        await monitor.stop()

    async def test_metrics_polling_reports_counts(self):
        sink = Sink()
        monitor = WorkerMonitor("ep1", "calc", sink, metrics_key="mk")
        payload = {"workers": {"initializing": 1, "ready": 0}}

        async def fake_get(url, headers, timeout):
            assert headers["Authorization"] == "Bearer mk"
            return payload

        with patch("runpod.apps.utils.network.get_json", fake_get):
            await monitor.start()
            await asyncio.sleep(0.05)
            await monitor.stop()

        statuses = [e for e in sink.events if e[0] == "worker_status"]
        assert statuses
        assert statuses[0][2]["initializing"] == 1

