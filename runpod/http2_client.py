import httpx
import json
import time
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider, Span
from opentelemetry.sdk.trace.export import SpanExporter, SimpleSpanProcessor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from uuid import uuid4

from .serverless.modules.rp_logger import RunPodLogger

log = RunPodLogger()


# Custom Span Exporter to log trace data to logger
class JSONLoggingSpanExporter(SpanExporter):
    def export(self, spans: list[Span]):
        for span in spans:
            # Accumulate span data into a dictionary
            span_data = {
                "span_name": span.name,
                "trace_id": format(span.context.trace_id, "032x"),
                "span_id": format(span.context.span_id, "016x"),
                "start_time": span.start_time.isoformat(),
                "end_time": span.end_time.isoformat(),
                "attributes": span.attributes
            }
            
            # Log the span as a single JSON object
            log.info(json.dumps(span_data))

        return SpanExporter.SUCCESS

    def shutdown(self):
        pass


# Set up tracing and use custom logging span exporter
def setup_tracing():
    provider = TracerProvider(
        resource=Resource.create({"service.name": "httpx-client"})
    )
    trace.set_tracer_provider(provider)

    # Create a custom span processor with the logging span exporter
    logging_span_processor = SimpleSpanProcessor(LoggingSpanExporter())
    provider.add_span_processor(logging_span_processor)


# Hook to add request_id, capture timing, and connection stats
async def on_request(request: httpx.Request):
    # Get the current span and record request start time
    span = trace.get_current_span()
    if span.is_recording():
        request_id = str(uuid4())
        span.set_attribute("request_id", request_id)
        request.context["start_time"] = time.time()  # Store the start time

        # Log the request being made and request_id
        log.info(f"Added request_id: {request_id} to request {request.url}")


# Hook to capture response stats
async def on_response(response: httpx.Response):
    span = trace.get_current_span()
    if span.is_recording():
        # Calculate the total time and capture response size
        start_time = response.request.context["start_time"]
        total_time = time.time() - start_time
        span.set_attribute("http.total_time", total_time)
        span.set_attribute("http.status_code", response.status_code)
        span.set_attribute("http.response_size", len(response.content))

        # Log the times and response status
        log.info(
            f"Response received: {response.status_code} in {total_time:.2f} seconds"
        )


# Hook to capture errors
async def on_error(request: httpx.Request, exception: Exception):
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("http.exception", str(exception))
        log.error(f"Error occurred: {exception} for request {request.url}")


# Set up OpenTelemetry tracing with custom logger
setup_tracing()


# Instrument httpx with OpenTelemetry
HTTPXClientInstrumentor().instrument()


def AsyncClientSession(*args, **kwargs):
    return httpx.AsyncClient(
        http2=True,
        event_hooks={
            "request": [on_request],
            "response": [on_response],
            "error": [on_error],
        },
        *args,
        **kwargs,
    )
