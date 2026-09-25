"""Runpod REST API transport."""

import math
import os
import time
from typing import Any, Iterator, Mapping, Optional

import requests

from runpod import error
from runpod.user_agent import USER_AGENT

HTTP_STATUS_NO_CONTENT = 204
HTTP_STATUS_BAD_REQUEST = 400
HTTP_STATUS_UNAUTHORIZED = 401
HTTP_STATUS_NOT_FOUND = 404
HTTP_STATUS_TOO_MANY_REQUESTS = 429
STREAM_IDLE_TIMEOUT = 60


def _resolve_api_key(api_key: Optional[str]) -> str:
    from runpod import api_key as global_api_key  # pylint: disable=import-outside-toplevel,cyclic-import

    effective_api_key = api_key or global_api_key
    if not effective_api_key:
        raise error.AuthenticationError("No API key provided")
    return effective_api_key


def _build_url(path: str) -> str:
    api_url_base = os.environ.get("RUNPOD_API_BASE_URL", "https://api.runpod.io")
    return f"{api_url_base.rstrip('/')}/{path.lstrip('/')}"


def _build_headers(api_key: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {api_key}",
    }


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _retry_after(response: requests.Response) -> Optional[float]:
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, TypeError, ValueError):
        return None


def _raise_for_error(
    response: requests.Response, method: str, path: str
) -> None:
    if response.status_code == HTTP_STATUS_UNAUTHORIZED:
        raise error.AuthenticationError(
            "Unauthorized request, please check your API key."
        )

    if response.status_code < HTTP_STATUS_BAD_REQUEST:
        return

    payload = _response_json(response)
    message = payload.get("detail") or payload.get("title")
    if not message:
        message = response.text or f"Request failed with status {response.status_code}"

    raise error.QueryError(
        str(message),
        f"{method.upper()} {path}",
        status_code=response.status_code,
        errors=payload.get("errors"),
        retry_after=_retry_after(response),
    )


def run_rest_request(
    method: str,
    path: str,
    *,
    api_key: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
    json: Optional[Mapping[str, Any]] = None,
    timeout: float = 30,
) -> Optional[dict[str, Any]]:
    """Send an authenticated request to the Runpod REST API."""
    response = requests.request(
        method,
        _build_url(path),
        headers=_build_headers(_resolve_api_key(api_key)),
        params=params,
        json=json,
        timeout=timeout,
    )
    _raise_for_error(response, method, path)

    if response.status_code == HTTP_STATUS_NO_CONTENT or not response.content:
        return None
    return response.json()


def _parse_event_stream(chunks: Iterator[bytes]) -> Iterator[dict[str, str]]:
    """Parse text/event-stream bytes into events with `id`, `event` and `data`.

    Only events terminated by a blank line are yielded, so an event cut off
    mid-frame when the stream is closed early is discarded.
    """
    buffer = b""
    event: dict[str, str] = {}
    data: list[str] = []
    for chunk in chunks:
        buffer += chunk
        *lines, buffer = buffer.replace(b"\r\n", b"\n").split(b"\n")
        for raw_line in lines:
            line = raw_line.decode("utf-8")
            if not line:
                if data:
                    event["data"] = "\n".join(data)
                    yield event
                event, data = {}, []
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "data":
                data.append(value)
            elif field in ("id", "event"):
                event[field] = value


def read_event_stream(
    path: str,
    *,
    api_key: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
    max_wait: Optional[float] = 5,
    last_event_id: Optional[str] = None,
) -> Iterator[dict[str, str]]:
    """Read events from a Runpod REST SSE endpoint for up to `max_wait` seconds.

    The endpoints behind this hold the connection open to tail live output, so
    the read ends at the deadline, or once the stream has been idle for
    `max_wait` seconds (`STREAM_IDLE_TIMEOUT` when `max_wait` is None, which
    sets no deadline). A timeout before the response headers arrive raises.
    `last_event_id` resumes the stream after that event.
    """
    deadline = math.inf if max_wait is None else time.monotonic() + max_wait
    headers = _build_headers(_resolve_api_key(api_key))
    headers["Accept"] = "text/event-stream"
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id

    with requests.get(
        _build_url(path),
        headers=headers,
        params=params,
        stream=True,
        timeout=(30, STREAM_IDLE_TIMEOUT if max_wait is None else max_wait),
    ) as response:
        _raise_for_error(response, "GET", path)

        def chunks() -> Iterator[bytes]:
            try:
                for chunk in response.iter_content(chunk_size=None):
                    yield chunk
                    if time.monotonic() >= deadline:
                        return
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
            ):
                # An idle-read timeout or a dropped connection ends the snapshot.
                return

        yield from _parse_event_stream(chunks())
