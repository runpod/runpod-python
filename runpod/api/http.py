"""shared HTTP sessions, request lifetimes, and response decoding."""

from contextlib import asynccontextmanager, contextmanager
from typing import Mapping

import aiohttp
import requests
from yarl import URL

from runpod.user_agent import USER_AGENT

from .routes import Route, api_base_url


async def _without_disconnect_replay(request, handler):
    try:
        return await handler(request)
    except aiohttp.ClientConnectorError:
        raise
    except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError) as exc:
        raise aiohttp.ClientConnectionError(str(exc)) from exc


class HTTPClient:
    """an async transport with an explicitly scoped connection pool."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        session: aiohttp.ClientSession | None = None,
    ):
        self.base_url = api_base_url() if base_url is None else base_url
        self.headers = {"User-Agent": USER_AGENT} if session is None else {}
        self.headers.update(headers or {})
        self.session = session
        self._owns_session = session is None

    async def __aenter__(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
        elif self.session.closed:
            raise RuntimeError("the supplied HTTP session is closed")
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        if self._owns_session and self.session is not None:
            session, self.session = self.session, None
            await session.close()

    @asynccontextmanager
    async def stream(self, route: Route, *, timeout=30, headers=None, **kwargs):
        if self.session is None:
            async with HTTPClient(self.base_url, headers=self.headers) as client:
                async with client.stream(
                    route, timeout=timeout, headers=headers, **kwargs
                ) as response:
                    yield response
            return
        if route.method in {"PUT", "DELETE"}:
            middlewares = kwargs.get("middlewares")
            if middlewares is None:
                middlewares = self.session._middlewares
            kwargs["middlewares"] = (*middlewares, _without_disconnect_replay)
        if not isinstance(timeout, aiohttp.ClientTimeout):
            timeout = aiohttp.ClientTimeout(total=timeout)
        kwargs.setdefault("allow_redirects", False)
        async with self.session.request(
            route.method,
            URL(route.url(self.base_url), encoded=True),
            headers={**self.headers, **(headers or {})},
            timeout=timeout,
            **kwargs,
        ) as response:
            yield response

    async def request(self, route: Route, *, response_type="json", **kwargs):
        if response_type not in {"json", "text"}:
            raise ValueError("response_type must be json or text")
        async with self.stream(route, **kwargs) as response:
            response.raise_for_status()
            if 300 <= response.status < 400:
                raise aiohttp.ClientResponseError(
                    response.request_info,
                    response.history,
                    status=response.status,
                    message="unexpected redirect",
                    headers=response.headers,
                )
            if response_type == "text":
                return await response.text()
            if response.status == 204 or not await response.read():
                return None
            return await response.json(content_type=None)


class SyncHTTPClient:
    """a synchronous transport with the same routes and ownership rules."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        session: requests.Session | None = None,
    ):
        self.base_url = api_base_url() if base_url is None else base_url
        self.headers = {"User-Agent": USER_AGENT} if session is None else {}
        self.headers.update(headers or {})
        self.session = session
        self._owns_session = session is None

    def __enter__(self):
        if self.session is None:
            self.session = requests.Session()
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self._owns_session and self.session is not None:
            session, self.session = self.session, None
            session.close()

    @contextmanager
    def stream(self, route: Route, *, timeout=30, headers=None, **kwargs):
        if self.session is None:
            with SyncHTTPClient(self.base_url, headers=self.headers) as client:
                with client.stream(
                    route, timeout=timeout, headers=headers, **kwargs
                ) as response:
                    yield response
            return
        kwargs.setdefault("allow_redirects", False)
        response = self.session.request(
            route.method,
            route.url(self.base_url),
            headers={**self.headers, **(headers or {})},
            timeout=timeout,
            **kwargs,
        )
        try:
            yield response
        finally:
            response.close()
