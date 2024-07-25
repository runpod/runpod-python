# pylint: disable-all
# Temporary tracer while we're still using aiohttp and requests
# TODO: use httpx and opentelemetry

import asyncio
import json
import types
from time import time
from uuid import uuid4
from requests import (
    Response,
    PreparedRequest,
    structures,
)
from aiohttp import (
    TraceConfig,
    TraceRequestStartParams,
    TraceConnectionCreateEndParams,
    TraceConnectionReuseconnParams,
    TraceRequestEndParams,
    TraceRequestExceptionParams,
    TraceRequestChunkSentParams,
    TraceResponseChunkReceivedParams,
)

from .serverless.modules.rp_logger import RunPodLogger


log = RunPodLogger()


def headers_to_context(context: types.SimpleNamespace, headers: dict):
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


async def on_request_start(session, context, params: TraceRequestStartParams):
    headers = params.headers if hasattr(params, "headers") else {}
    context = headers_to_context(context, headers)
    context.on_request_start = asyncio.get_event_loop().time()
    context.method = params.method
    context.url = params.url.human_repr()
    context.mode = "async"

    if hasattr(context, "trace_request_ctx") and context.trace_request_ctx:
        context.retries = context.trace_request_ctx["current_attempt"]


async def on_connection_create_end(
    session, context, params: TraceConnectionCreateEndParams
):
    context.connect = asyncio.get_event_loop().time() - context.on_request_start


async def on_connection_reuseconn(
    session, context, params: TraceConnectionReuseconnParams
):
    context.connect = asyncio.get_event_loop().time() - context.on_request_start


async def on_request_chunk_sent(session, context, params: TraceRequestChunkSentParams):
    if not hasattr(context, "payload_size_bytes"):
        context.payload_size_bytes = 0
    context.payload_size_bytes += len(params.chunk)


async def on_response_chunk_received(
    session, context, params: TraceResponseChunkReceivedParams
):
    if not hasattr(context, "response_size_bytes"):
        context.response_size_bytes = 0
    context.response_size_bytes += len(params.chunk)


async def on_request_end(session, context, params: TraceRequestEndParams):
    elapsed = asyncio.get_event_loop().time() - context.on_request_start
    context.transfer = elapsed - context.connect
    # log to trace level
    report_trace(context, params, elapsed)


async def on_request_exception(session, context, params: TraceRequestExceptionParams):
    context.exception = str(params.exception)
    elapsed = asyncio.get_event_loop().time() - context.on_request_start
    context.transfer = elapsed - context.connect
    # log to error level
    report_trace(context, params, elapsed, log.error)


def report_trace(context: types.SimpleNamespace, params, elapsed, logger=log.trace):
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


def get_aiohttp_tracer() -> TraceConfig:
    # https://docs.aiohttp.org/en/stable/tracing_reference.html
    trace_config = TraceConfig()

    trace_config.on_request_start.append(on_request_start)
    trace_config.on_connection_create_end.append(on_connection_create_end)
    trace_config.on_connection_reuseconn.append(on_connection_reuseconn)
    trace_config.on_request_chunk_sent.append(on_request_chunk_sent)
    trace_config.on_response_chunk_received.append(on_response_chunk_received)
    trace_config.on_request_end.append(on_request_end)
    trace_config.on_request_exception.append(on_request_exception)

    return trace_config


# Tracer for requests


class TraceRequest:
    def __init__(self):
        self.context = types.SimpleNamespace()
        self.request: PreparedRequest = None
        self.response: Response = None
        self.request_start = None

    def __enter__(self):
        self.request_start = time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.request is not None:
            self.context = headers_to_context(self.context, self.request.headers)
            self.context.method = self.request.method
            self.context.url = self.request.url
            self.context.mode = "sync"

            if isinstance(self.request.body, bytes):
                self.context.payload_size_bytes = len(self.request.body)

        if self.response is not None:
            request_end = time() - self.request_start
            self.context.transfer = self.response.elapsed.total_seconds()
            self.context.connect = request_end - self.context.transfer

            self.context.response_status = self.response.status_code
            self.context.response_size_bytes = len(self.response.content)

            if hasattr(self.response.raw, "retries"):
                self.context.retries = self.response.raw.retries.total

            logger = log.trace if self.response.ok else log.error
            report_trace(self.context, {}, request_end, logger)


def get_request_tracer():
    return TraceRequest()
