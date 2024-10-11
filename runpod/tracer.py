# pylint: disable-all
# Temporary tracer while we're still using aiohttp and requests
# TODO: use httpx and opentelemetry

import asyncio
import json
from datetime import datetime, timezone
from time import monotonic, time
from types import SimpleNamespace
from uuid import uuid4

from aiohttp import (
    ClientSession,
    TraceConfig,
    TraceConnectionCreateEndParams,
    TraceConnectionCreateStartParams,
    TraceConnectionReuseconnParams,
    TraceRequestChunkSentParams,
    TraceRequestEndParams,
    TraceRequestExceptionParams,
    TraceRequestStartParams,
    TraceResponseChunkReceivedParams,
)
from requests import PreparedRequest, Response, structures

from .serverless.modules.rp_logger import RunPodLogger

log = RunPodLogger()


def time_to_iso8601(ts: float) -> str:
    """Convert a Unix timestamp to an ISO 8601 formatted string in UTC."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.isoformat()


def headers_to_context(context: SimpleNamespace, headers: dict):
    """Generate a context object based on the provided headers."""
    context.trace_id = str(uuid4())
    context.request_id = None
    context.user_agent = None

    if headers:
        headers = structures.CaseInsensitiveDict(headers)
        context.trace_id = headers.get("x-trace-id", context.trace_id)
        context.request_id = headers.get("x-request-id")
        context.user_agent = headers.get("user-agent")

    return context


# Tracer for aiohttp


async def on_request_start(
    session: ClientSession,
    context: SimpleNamespace,
    params: TraceRequestStartParams,
):
    """Handle the start of a request."""
    headers = params.headers if hasattr(params, "headers") else {}
    context = headers_to_context(context, headers)
    context.start_time = time()
    context.on_request_start = asyncio.get_event_loop().time()
    context.method = params.method
    context.url = params.url.human_repr()
    context.mode = "async"

    if hasattr(context, "trace_request_ctx") and context.trace_request_ctx:
        context.retries = context.trace_request_ctx["current_attempt"]


async def on_connection_create_start(
    session: ClientSession,
    context: SimpleNamespace,
    params: TraceConnectionCreateStartParams,
):
    """Handle the event when a connection is started."""
    context.connect = asyncio.get_event_loop().time() - context.on_request_start


async def on_connection_create_end(
    session: ClientSession,
    context: SimpleNamespace,
    params: TraceConnectionCreateEndParams,
):
    """Handle the event when a connection is created."""
    context.connect = asyncio.get_event_loop().time() - context.on_request_start


async def on_connection_reuseconn(
    session: ClientSession,
    context: SimpleNamespace,
    params: TraceConnectionReuseconnParams,
):
    """Handle the event when a connection is reused."""
    context.connect = asyncio.get_event_loop().time() - context.on_request_start


async def on_request_chunk_sent(
    session: ClientSession,
    context: SimpleNamespace,
    params: TraceRequestChunkSentParams,
):
    """Handle the event when a request chunk is sent."""
    if not hasattr(context, "payload_size_bytes"):
        context.payload_size_bytes = 0
    context.payload_size_bytes += len(params.chunk)


async def on_response_chunk_received(
    session: ClientSession,
    context: SimpleNamespace,
    params: TraceResponseChunkReceivedParams,
):
    """Handle the event when a response chunk is received."""
    if not hasattr(context, "response_size_bytes"):
        context.response_size_bytes = 0
    context.response_size_bytes += len(params.chunk)


async def on_request_end(
    session: ClientSession,
    context: SimpleNamespace,
    params: TraceRequestEndParams,
):
    """Handle the end of a request."""
    elapsed = asyncio.get_event_loop().time() - context.on_request_start
    context.transfer = elapsed - context.connect
    context.end_time = time()

    # log to trace level
    report_trace(context, params, elapsed)


async def on_request_exception(
    session: ClientSession,
    context: SimpleNamespace,
    params: TraceRequestExceptionParams,
):
    """Handle the exception that occurred during the request."""
    context.exception = params.exception
    elapsed = asyncio.get_event_loop().time() - context.on_request_start
    context.transfer = elapsed - context.connect
    context.end_time = time()

    # log to error level
    report_trace(context, params, elapsed, log.trace)


def report_trace(
    context: SimpleNamespace, params: object, elapsed: float, logger=log.trace
):
    """
    Report the trace of a request.
    The logger function is called with the JSON representation of the context object and the request ID.

    Args:
        context (SimpleNamespace): The context object containing trace information.
        params: The parameters of the request.
        elapsed (float): The elapsed time of the request.
        logger (function, optional): The logger function to use. Defaults to log.trace.
    """
    context.start_time = time_to_iso8601(context.start_time)
    context.end_time = time_to_iso8601(context.end_time)
    context.total = round(elapsed * 1000, 1)

    if hasattr(context, "transfer") and context.transfer:
        context.transfer = round(context.transfer * 1000, 1)

    if hasattr(context, "connect") and context.connect:
        context.connect = round(context.connect * 1000, 1)

    if hasattr(context, "on_request_start"):
        delattr(context, "on_request_start")

    if hasattr(context, "trace_request_ctx"):
        delattr(context, "trace_request_ctx")

    if hasattr(params, "response") and params.response:
        context.response_status = params.response.status

    logger(json.dumps(vars(context)), context.request_id)


def create_aiohttp_tracer() -> TraceConfig:
    """
    Creates a TraceConfig object for aiohttp tracing.

    This function initializes a TraceConfig object with event handlers for various tracing events.
    The TraceConfig object is used to configure and customize the tracing behavior of aiohttp.

    Returns:
        TraceConfig: The initialized TraceConfig object.

    """
    # https://docs.aiohttp.org/en/stable/tracing_reference.html
    trace_config = TraceConfig()

    trace_config.on_request_start.append(on_request_start)
    trace_config.on_connection_create_start.append(on_connection_create_start)
    trace_config.on_connection_create_end.append(on_connection_create_end)
    trace_config.on_connection_reuseconn.append(on_connection_reuseconn)
    trace_config.on_request_chunk_sent.append(on_request_chunk_sent)
    trace_config.on_response_chunk_received.append(on_response_chunk_received)
    trace_config.on_request_end.append(on_request_end)
    trace_config.on_request_exception.append(on_request_exception)

    return trace_config


# Tracer for requests


class TraceRequest:
    """
    Context manager for tracing requests.

    This class is used to trace requests made by the `requests` library.
    It stores the request and response objects in the `request` and `response`
    attributes respectively. It also provides a context manager interface
    allowing the tracing of requests, including the connection and transfer
    times.

    When the context manager is entered, the request start time is recorded.
    When the context manager is exited, the trace is reported.

    Attributes:
        context (SimpleNamespace): The context object used to store
            trace information.
        request (PreparedRequest): The request object.
        response (Response): The response object.
        request_start (float): The start time of the request.
    """

    def __init__(self):
        self.context = SimpleNamespace()
        self.request: PreparedRequest = None
        self.response: Response = None
        self.request_start = None

    def __enter__(self):
        """
        Enter the context manager and record the start time of the request.
        """
        self.request_start = (
            monotonic()
        )  # consistency with asyncio.get_event_loop().time()
        self.context.start_time = time()  # reported timestamp
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Exit the context manager and report the trace.
        """
        if self.request is not None:
            self.context = headers_to_context(self.context, self.request.headers)
            self.context.method = self.request.method
            self.context.url = self.request.url
            self.context.mode = "sync"

            if hasattr(self.request, "body") and \
                self.request.body and \
                isinstance(self.request.body, bytes):
                self.context.payload_size_bytes = len(self.request.body)

        if self.response is not None:
            self.context.end_time = time()
            request_end = monotonic() - self.request_start
            self.context.transfer = self.response.elapsed.total_seconds()
            self.context.connect = request_end - self.context.transfer

            self.context.response_status = self.response.status_code
            self.context.response_size_bytes = len(self.response.content)

            if hasattr(self.response.raw, "retries"):
                self.context.retries = self.response.raw.retries.total

            logger = log.trace if self.response.ok else log.error
            report_trace(self.context, {}, request_end, logger)


def create_request_tracer():
    """
    This function creates and returns a new instance of the `TraceRequest` class.
    The `TraceRequest` class is used to trace the execution of a request in a context manager.

    Returns:
        TraceRequest: An instance of the `TraceRequest` class.

    Example:
        >>> with get_request_tracer() as tracer:
        ...     result = requests.get("https://example.com")
        ...     tracer.response = result.response
    """
    return TraceRequest()
