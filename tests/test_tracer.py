# pylint: disable-all
# Temporary tracer while we're still using aiohttp and requests
# TODO: use httpx and opentelemetry
import asyncio
import json
import unittest
from time import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aiohttp import (
    TraceConfig,
    TraceConnectionCreateEndParams,
    TraceConnectionCreateStartParams,
    TraceConnectionReuseconnParams,
    TraceRequestChunkSentParams,
    TraceRequestExceptionParams,
    TraceRequestStartParams,
    TraceResponseChunkReceivedParams,
)
from yarl import URL

from runpod.tracer import (
    create_aiohttp_tracer,
    on_connection_create_end,
    on_connection_create_start,
    on_connection_reuseconn,
    on_request_chunk_sent,
    on_request_end,
    on_request_exception,
    on_request_start,
    on_response_chunk_received,
    report_trace,
    time_to_iso8601,
)


class TestTracer(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_get_aiohttp_tracer(self):
        assert isinstance(create_aiohttp_tracer(), TraceConfig)

    def test_on_request_start(self):
        session = MagicMock()
        context = SimpleNamespace(trace_request_ctx={"current_attempt": 0})
        params = TraceRequestStartParams(
            "GET", URL("http://test.com/"), {"X-Request-ID": "myRequestId"}
        )

        self.loop.run_until_complete(on_request_start(session, context, params))
        assert hasattr(context, "on_request_start")
        assert hasattr(context, "trace_id")
        assert context.method == params.method
        assert context.url == params.url.human_repr()

    def test_on_connection_create_start(self):
        session = MagicMock()
        context = SimpleNamespace(on_request_start=self.loop.time())
        params = TraceConnectionCreateStartParams()

        self.loop.run_until_complete(
            on_connection_create_start(session, context, params)
        )

        assert context.connect

    def test_on_connection_create_end(self):
        session = MagicMock()
        context = SimpleNamespace(on_request_start=self.loop.time())
        params = TraceConnectionCreateEndParams()

        self.loop.run_until_complete(on_connection_create_end(session, context, params))

        assert context.connect

    def test_on_connection_reuseconn(self):
        session = MagicMock()
        context = SimpleNamespace(on_request_start=self.loop.time())
        params = TraceConnectionReuseconnParams()

        self.loop.run_until_complete(on_connection_reuseconn(session, context, params))

        assert context.connect

    def test_on_request_chunk_sent(self):
        session = MagicMock()
        context = SimpleNamespace(on_request_start=self.loop.time())
        params = TraceRequestChunkSentParams(
            "GET", URL("http://test.com/"), chunk=b"test data"
        )

        # Initial call to on_request_start to initialize context
        self.loop.run_until_complete(on_request_start(session, context, params))

        # Call on_request_chunk_sent multiple times to simulate multiple chunks being sent
        for _ in range(3):
            self.loop.run_until_complete(
                on_request_chunk_sent(session, context, params)
            )

        # Verify that payload_size_bytes has accumulated
        assert context.payload_size_bytes == len(params.chunk) * 3

    def test_on_response_chunk_received(self):
        session = MagicMock()
        context = SimpleNamespace(on_request_start=self.loop.time())
        params = TraceResponseChunkReceivedParams(
            "GET", URL("http://test.com/"), chunk=b"received data"
        )

        # Initial call to on_request_start to initialize context
        self.loop.run_until_complete(on_request_start(session, context, params))

        # Call on_response_chunk_received multiple times to simulate multiple chunks being received
        for _ in range(3):
            self.loop.run_until_complete(
                on_response_chunk_received(session, context, params)
            )

        # Verify that payload_size_bytes has accumulated
        assert context.response_size_bytes == len(params.chunk) * 3

    @patch("runpod.tracer.report_trace")
    def test_on_request_end(self, mock_report_trace):
        session = MagicMock()
        context = SimpleNamespace(on_request_start=self.loop.time(), connect=0.5)
        params = MagicMock()

        self.loop.run_until_complete(on_request_end(session, context, params))
        mock_report_trace.assert_called_once()

    @patch("runpod.tracer.report_trace")
    def test_on_request_exception(self, mock_report_trace):
        session = MagicMock()
        context = SimpleNamespace(on_request_start=self.loop.time(), connect=0.5)
        params = TraceRequestExceptionParams(
            "GET",
            URL("http://test.com/"),
            headers={},
            exception=Exception("Test Exception"),
        )

        self.loop.run_until_complete(on_request_exception(session, context, params))
        mock_report_trace.assert_called_once()
        assert context.exception

    @patch("runpod.tracer.log")
    def test_report_trace(self, mock_log):
        context = SimpleNamespace()
        context.trace_id = "test-trace-id"
        context.request_id = "test-request-id"
        context.start_time = time()
        context.end_time = time() + 2
        context.connect = 0.5
        context.payload_size_bytes = 1024
        context.response_size_bytes = 2048
        context.retries = 0
        context.trace_request_ctx = {"current_attempt": 0}
        context.transfer = 1.0

        params = MagicMock()
        params.response.status = 200

        elapsed = 1.5

        expected_report = {
            "trace_id": "test-trace-id",
            "request_id": "test-request-id",
            "connect": 500.0,
            "payload_size_bytes": 1024,
            "response_size_bytes": 2048,
            "retries": 0,
            "start_time": time_to_iso8601(context.start_time),
            "end_time": time_to_iso8601(context.end_time),
            "total": 1500.0,  # 1.5 seconds to milliseconds
            "transfer": 1000.0,  # 1.5 - 0.5 seconds to milliseconds
            "response_status": 200,
        }

        report_trace(context, params, elapsed, mock_log.trace)

        assert expected_report == json.loads(mock_log.trace.call_args[0][0])

    @patch("runpod.tracer.log")
    def test_report_trace_error_log(self, mock_log):
        context = SimpleNamespace()
        context.trace_id = "test-trace-id"
        context.request_id = "test-request-id"
        context.start_time = time()
        context.end_time = time() + 2
        context.connect = 0.5
        context.retries = 3
        context.trace_request_ctx = {"current_attempt": 3}
        context.exception = str(Exception("Test Exception"))
        context.transfer = 1.0

        params = MagicMock()
        params.response.status = 502

        elapsed = 1.5

        expected_report = {
            "trace_id": "test-trace-id",
            "request_id": "test-request-id",
            "connect": 500.0,
            "retries": 3,
            "exception": "Test Exception",
            "start_time": time_to_iso8601(context.start_time),
            "end_time": time_to_iso8601(context.end_time),
            "total": 1500.0,  # 1.5 seconds to milliseconds
            "transfer": 1000.0,  # 1.5 - 0.5 seconds to milliseconds
            "response_status": 502,
        }

        report_trace(context, params, elapsed, mock_log.error)

        assert expected_report == json.loads(mock_log.error.call_args[0][0])
