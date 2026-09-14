"""Asynchronous REST and streaming transport for CPU sandboxes."""

from __future__ import annotations

import asyncio
import codecs
import json
import math
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp
from yarl import URL

from runpod import error
from runpod.api.rest import (
    _build_headers,
    _build_url,
    _raise_for_status,
    _resolve_api_key,
)

_SANDBOX_PATH = "/v2/sandboxes"
_LINE_END = re.compile(r"\r\n|\r|\n")


def _sandbox_path(sandbox_id: str) -> str:
    return f"{_SANDBOX_PATH}/{quote(sandbox_id, safe='')}"


async def _raise_response_error(
    response: aiohttp.ClientResponse, method: str, path: str
) -> None:
    if response.status == 401:
        _raise_for_status(response.status, {}, "", method, path)
    if 200 <= response.status < 300:
        return
    raw = await response.read()
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        text = raw.decode(response.charset or "utf-8", errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    _raise_for_status(response.status, payload, text, method, path)
    # Redirects are deliberately not followed, including on read operations.
    raise error.QueryError(
        text or f"Unexpected HTTP status {response.status}",
        f"{method} {path}",
        status_code=response.status,
        errors=payload.get("errors"),
    )


async def _sse_lines(content: aiohttp.StreamReader) -> AsyncIterator[str]:
    """Decode only the current line, allowing arbitrary UTF-8/chunk boundaries."""
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    pending = ""
    skip_lf = False
    async for chunk in content.iter_chunked(8192):
        text = decoder.decode(chunk)
        if not text:
            continue
        if skip_lf:
            if text.startswith("\n"):
                text = text[1:]
            skip_lf = False
        text = pending + text
        start = 0
        for match in _LINE_END.finditer(text):
            yield text[start : match.start()]
            start = match.end()
        skip_lf = text.endswith("\r")
        pending = text[start:]
    pending += decoder.decode(b"", final=True)
    if pending:
        yield pending


class AsyncSandboxLogStream:
    """One SSE connection; use an async context or ``aclose`` on early exit.

    ``open`` performs the HTTP handshake without waiting for a log event.
    Exhaustion, cancellation, parsing errors and server timeout frames close
    the response. No reconnect is attempted, even when a cursor is supplied.
    """

    def __init__(
        self,
        api: AsyncSandboxAPI,
        path: str,
        params: list[tuple[str, str]],
        last_event_id: str | None,
    ) -> None:
        self._api = api
        self._path = path
        self._params = params
        self._last_event_id = last_event_id
        self._response: aiohttp.ClientResponse | None = None
        self._open_task: asyncio.Task[None] | None = None
        self._iterator: AsyncIterator[dict[str, Any]] | None = None
        self._closed = False

    async def _perform_open(self) -> None:
        try:
            if self._closed:
                raise RuntimeError("Sandbox log stream is closed")
            headers = self._api._headers()
            headers["Accept"] = "text/event-stream"
            if self._last_event_id is not None:
                headers["Last-Event-ID"] = self._last_event_id
            self._response = await self._api._get_session().request(
                "GET",
                self._api._url(self._path, self._params),
                headers=headers,
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    connect=self._api.request_timeout,
                    sock_connect=self._api.request_timeout,
                    sock_read=None,
                ),
                allow_redirects=False,
            )
            await _raise_response_error(self._response, "GET", self._path)
            if self._response.content_type != "text/event-stream":
                raise error.QueryError(
                    "Expected a text/event-stream sandbox log response",
                    f"GET {self._path}",
                    status_code=self._response.status,
                )
            self._iterator = self._events(self._response)
        except BaseException:
            self._dispose()
            raise

    async def open(self) -> AsyncSandboxLogStream:
        """Open once, with a bounded handshake but no total stream timeout."""
        if self._closed:
            raise RuntimeError("Sandbox log stream is closed")
        if self._open_task is None:
            self._open_task = asyncio.create_task(
                asyncio.wait_for(
                    self._perform_open(), timeout=self._api.request_timeout
                )
            )
        try:
            await self._open_task
        except BaseException:
            await self.aclose()
            raise
        return self

    def _dispose(self) -> None:
        self._closed = True
        if self._response is not None:
            self._response.close()
            self._response = None
        self._iterator = None
        self._api._streams.discard(self)

    async def aclose(self) -> None:
        """Release this stream, including a handshake still in progress."""
        self._dispose()
        if self._open_task is not None and not self._open_task.done():
            self._open_task.cancel()
            await asyncio.gather(self._open_task, return_exceptions=True)

    async def __aenter__(self) -> AsyncSandboxLogStream:
        return await self.open()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.aclose()

    def __aiter__(self) -> AsyncSandboxLogStream:
        return self

    async def __anext__(self) -> dict[str, Any]:
        if self._closed:
            raise StopAsyncIteration
        try:
            await self.open()
            assert self._iterator is not None
            return await anext(self._iterator)
        except BaseException:
            await self.aclose()
            raise

    async def _events(
        self, response: aiohttp.ClientResponse
    ) -> AsyncIterator[dict[str, Any]]:
        data: list[str] = []
        event_type = ""
        event_id: str | None = None
        async for line in _sse_lines(response.content):
            if not line:
                if event_type == "timeout":
                    return
                if event_type not in ("", "message"):
                    raise error.QueryError(
                        f"Sandbox log event {event_type!r}: " + "\n".join(data),
                        f"GET {self._path}",
                    )
                if data:
                    payload = json.loads("\n".join(data))
                    if not isinstance(payload, dict):
                        raise ValueError("Sandbox log event must be a JSON object")
                    yield {
                        "source": payload["source"],
                        "line": payload["line"],
                        "ts": payload["ts"],
                        "id": event_id,
                    }
                data = []
                event_type = ""
                continue
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if separator and value.startswith(" "):
                value = value[1:]
            if field == "data":
                data.append(value)
            elif field == "event":
                event_type = value
            elif field == "id" and "\x00" not in value:
                event_id = value
            # SSE retry directives are intentionally ignored: no reconnects.
        # Per SSE, EOF does not dispatch an event lacking its blank-line boundary.


class AsyncSandboxAPI:
    """Lazy, owned aiohttp transport. Each operation makes one HTTP request.

    ``close`` releases streams and sockets, but a later request can open a new
    session. Use the instance from one event loop while its session is active.
    Readiness and retry policies belong to the higher-level sandbox handles.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        request_timeout: float = 30,
    ) -> None:
        if not math.isfinite(request_timeout) or request_timeout <= 0:
            raise ValueError("request_timeout must be a finite positive number")
        self.api_key = api_key
        self.base_url = base_url
        self.request_timeout = request_timeout
        self._session: aiohttp.ClientSession | None = None
        self._streams: set[AsyncSandboxLogStream] = set()

    def _headers(self) -> dict[str, str]:
        return _build_headers(_resolve_api_key(self.api_key))

    def _url(self, path: str, params: list[tuple[str, str]] | None = None) -> URL:
        url = _build_url(path, self.base_url)
        if params:
            url += "?" + urlencode(params, safe="=")
        # Preserve quoted ID segments (including encoded slashes) verbatim.
        return URL(url, encoded=True)

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.request_timeout),
                cookie_jar=aiohttp.DummyCookieJar(),
            )
            # aiohttp otherwise transparently replays disconnected GET/DELETE
            # requests. It currently exposes no public switch for this policy.
            self._session._retry_connection = False
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str]] | None = None,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        headers = self._headers()
        async with self._get_session().request(
            method,
            self._url(path, params),
            headers=headers,
            json=body,
            allow_redirects=False,
        ) as response:
            await _raise_response_error(response, method, path)
            if response.status == 204:
                return None
            payload = await response.json(content_type=None)
            if not isinstance(payload, dict):
                raise ValueError("Sandbox API response must be a JSON object")
            return payload

    async def create(self, body: Mapping[str, Any]) -> dict[str, Any]:
        payload = await self._request(
            "POST",
            _SANDBOX_PATH,
            body={key: value for key, value in body.items() if value is not None},
        )
        if payload is None:
            raise ValueError("Sandbox creation returned no snapshot")
        return payload

    async def list(
        self,
        *,
        state: str | None = None,
        labels: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        params = []
        if state is not None:
            params.append(("state", state))
        if labels is not None:
            params.extend(("labels", f"{key}={value}") for key, value in labels.items())
        payload = await self._request("GET", _SANDBOX_PATH, params=params)
        if payload is None:
            raise ValueError("Sandbox listing returned no response")
        sandboxes = payload["sandboxes"]
        if not isinstance(sandboxes, list) or any(
            not isinstance(item, dict) for item in sandboxes
        ):
            raise ValueError("Sandbox listing must contain an array of objects")
        return sandboxes

    async def get(self, sandbox_id: str) -> dict[str, Any]:
        payload = await self._request("GET", _sandbox_path(sandbox_id))
        if payload is None:
            raise ValueError("Sandbox lookup returned no snapshot")
        return payload

    async def terminate(self, sandbox_id: str) -> None:
        await self._request("DELETE", _sandbox_path(sandbox_id))

    async def exec(self, sandbox_id: str, command: Sequence[str]) -> dict[str, Any]:
        payload = await self._request(
            "POST", f"{_sandbox_path(sandbox_id)}/exec", body={"command": list(command)}
        )
        if payload is None:
            raise ValueError("Sandbox execution returned no result")
        return payload

    def logs(
        self,
        sandbox_id: str,
        *,
        source: str | None = None,
        tail: int | None = None,
        since: str | None = None,
        last_event_id: str | None = None,
    ) -> AsyncSandboxLogStream:
        params = [
            (key, str(value))
            for key, value in (("source", source), ("tail", tail), ("since", since))
            if value is not None
        ]
        stream = AsyncSandboxLogStream(
            self, f"{_sandbox_path(sandbox_id)}/logs", params, last_event_id
        )
        self._streams.add(stream)
        return stream

    async def close(self) -> None:
        """Close all active streams and the owned session; allow later reuse."""
        streams = tuple(self._streams)
        session, self._session = self._session, None
        # Dispose every response before the first await, including on cancellation.
        for stream in streams:
            stream._dispose()
            if stream._open_task is not None and not stream._open_task.done():
                stream._open_task.cancel()
        try:
            for stream in streams:
                await stream.aclose()
        finally:
            if session is not None:
                await session.close()
