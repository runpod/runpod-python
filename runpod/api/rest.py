"""Runpod REST API transport."""

import asyncio
import json as json_module
import time
from concurrent.futures import Future
from typing import Any, Mapping, Optional

import aiohttp

from runpod import error
from runpod.auth import resolve_api_key as _resolve_api_key
from runpod.user_agent import USER_AGENT

from ._rate_limit import _RetryBudget, _validate_retry_options, rate_limits
from .http import HTTPClient, SyncHTTPClient
from .routes import Route

HTTP_STATUS_NO_CONTENT = 204
HTTP_STATUS_BAD_REQUEST = 400
HTTP_STATUS_UNAUTHORIZED = 401
HTTP_STATUS_NOT_FOUND = 404


def _build_url(path: str) -> str:
    return Route("GET", path).url()


def _build_headers(api_key: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {api_key}",
    }


def _raise_for_error(
    status_code: int,
    method: str,
    path: str,
    text: str = "",
    retry_after: Optional[float] = None,
) -> None:
    if 300 <= status_code < 400:
        raise error.QueryError("unexpected redirect", status_code=status_code)
    if status_code == HTTP_STATUS_UNAUTHORIZED:
        raise error.AuthenticationError(
            "Unauthorized request, please check your API key."
        )

    if status_code < HTTP_STATUS_BAD_REQUEST:
        return

    try:
        payload = json_module.loads(text)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    message = payload.get("detail") or payload.get("title")
    if not message:
        message = text or f"Request failed with status {status_code}"

    if status_code == 429:
        raise error.RateLimitError(
            str(message),
            f"{method.upper()} {path}",
            retry_after=retry_after or 0.0,
            errors=payload.get("errors"),
        )

    raise error.QueryError(
        str(message),
        f"{method.upper()} {path}",
        status_code=status_code,
        errors=payload.get("errors"),
    )


class RESTClient(SyncHTTPClient):
    """reusable synchronous REST requests with shared quota handling."""

    def __init__(self, api_key=None, *, base_url=None, session=None):
        super().__init__(base_url, session=session)
        self.api_key = api_key

    def request(
        self,
        route: Route,
        *,
        api_key: Optional[str] = None,
        params: Optional[Mapping[str, Any]] = None,
        json: Optional[Mapping[str, Any]] = None,
        timeout: float = 30,
        max_retries: int = 3,
        max_rate_limit_wait: float = 60,
        user_agent: str = USER_AGENT,
    ) -> Optional[dict[str, Any]]:
        """Send a REST request with bounded 429 retries and shared quota waits.

        ``timeout`` applies to each HTTP attempt. ``max_rate_limit_wait`` bounds
        cumulative quota sleeps in seconds; ``max_retries`` counts 429 retries.
        Network failures and other HTTP errors are not retried.
        """
        method, path = route.method, route.resolved_path
        _validate_retry_options(max_retries, max_rate_limit_wait)
        key = _resolve_api_key(api_key or self.api_key)
        url = route.url(self.base_url)
        headers = _build_headers(key)
        headers["User-Agent"] = user_agent
        budget = _RetryBudget(
            max_retries, max_rate_limit_wait, f"{method.upper()} {path}"
        )
        with rate_limits.bucket(url, key) as bucket:
            for attempt in range(max_retries + 1):
                while wait := bucket.acquire(budget):
                    if isinstance(wait, Future):
                        wait.result()
                    else:
                        time.sleep(wait)
                try:
                    started_at = time.monotonic()
                    with self.stream(
                        route,
                        headers=headers,
                        params=params,
                        json=json,
                        timeout=timeout,
                    ) as response:
                        retry_after = bucket.update(
                            response.status_code,
                            response.headers,
                            attempt,
                            started_at=started_at,
                        )
                        if budget.can_retry(attempt, retry_after):
                            continue
                        if response.status_code >= 300:
                            _raise_for_error(
                                response.status_code,
                                method,
                                path,
                                response.text,
                                retry_after,
                            )
                        if (
                            response.status_code == HTTP_STATUS_NO_CONTENT
                            or not response.content
                        ):
                            return None
                        return response.json()
                finally:
                    bucket.release()


class AsyncRESTClient(HTTPClient):
    """reusable async REST requests with cancellable quota handling."""

    def __init__(self, api_key=None, *, base_url=None, session=None):
        super().__init__(base_url, session=session)
        self.api_key = api_key

    async def request(
        self,
        route: Route,
        *,
        api_key: Optional[str] = None,
        params: Optional[Mapping[str, Any]] = None,
        json: Optional[Mapping[str, Any]] = None,
        timeout: float = 30,
        max_retries: int = 3,
        max_rate_limit_wait: float = 60,
        user_agent: str = USER_AGENT,
    ) -> Optional[dict[str, Any]]:
        """Send a REST request with cancellable quota waits and bounded 429 retries.

        ``timeout`` applies to each HTTP attempt. ``max_rate_limit_wait`` bounds
        cumulative quota sleeps in seconds; ``max_retries`` counts 429 retries.
        Network failures and other HTTP errors are not retried.
        """
        method, path = route.method, route.resolved_path
        _validate_retry_options(max_retries, max_rate_limit_wait)
        key = _resolve_api_key(api_key or self.api_key)
        url = route.url(self.base_url)
        headers = _build_headers(key)
        headers["User-Agent"] = user_agent
        budget = _RetryBudget(
            max_retries, max_rate_limit_wait, f"{method.upper()} {path}"
        )
        with rate_limits.bucket(url, key) as bucket:
            for attempt in range(max_retries + 1):
                while wait := bucket.acquire(budget):
                    if isinstance(wait, Future):
                        await asyncio.wrap_future(wait)
                    else:
                        await asyncio.sleep(wait)
                try:
                    started_at = time.monotonic()
                    async with self.stream(
                        route,
                        headers=headers,
                        params=params,
                        json=json,
                        timeout=timeout,
                    ) as response:
                        retry_after = bucket.update(
                            response.status,
                            response.headers,
                            attempt,
                            started_at=started_at,
                        )
                        if budget.can_retry(attempt, retry_after):
                            continue
                        if response.status >= 300:
                            text = (
                                ""
                                if response.status == HTTP_STATUS_UNAUTHORIZED
                                else await response.text(errors="replace")
                            )
                            _raise_for_error(
                                response.status, method, path, text, retry_after
                            )
                        if (
                            response.status == HTTP_STATUS_NO_CONTENT
                            or not await response.read()
                        ):
                            return None
                        return await response.json(content_type=None)
                finally:
                    bucket.release()


def run_rest_request(
    method: str,
    path: str,
    *,
    api_key: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
    json: Optional[Mapping[str, Any]] = None,
    timeout: float = 30,
    max_retries: int = 3,
    max_rate_limit_wait: float = 60,
    user_agent: str = USER_AGENT,
) -> Optional[dict[str, Any]]:
    """send one REST request and close its connection pool."""
    with RESTClient(api_key) as client:
        return client.request(
            Route(method, path),
            params=params,
            json=json,
            timeout=timeout,
            max_retries=max_retries,
            max_rate_limit_wait=max_rate_limit_wait,
            user_agent=user_agent,
        )


async def run_rest_request_async(
    method: str,
    path: str,
    *,
    api_key: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
    json: Optional[Mapping[str, Any]] = None,
    timeout: float = 30,
    max_retries: int = 3,
    max_rate_limit_wait: float = 60,
    user_agent: str = USER_AGENT,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[dict[str, Any]]:
    """send one REST request using an owned or borrowed connection pool."""
    async with AsyncRESTClient(api_key, session=session) as client:
        return await client.request(
            Route(method, path),
            params=params,
            json=json,
            timeout=timeout,
            max_retries=max_retries,
            max_rate_limit_wait=max_rate_limit_wait,
            user_agent=user_agent,
        )


async def run_rest_paginated_request_async(
    path: str,
    collection: str,
    *,
    api_key: Optional[str] = None,
    user_agent: str = USER_AGENT,
) -> list[dict[str, Any]]:
    """read all cursor pages with one session and four transport attempts per page."""
    records = []
    params = {}
    seen_cursors = set()
    query = f"GET {path}"
    async with HTTPClient() as client:
        while True:
            for attempt in range(4):
                try:
                    data = await run_rest_request_async(
                        "GET",
                        path,
                        api_key=api_key,
                        params=params,
                        session=client.session,
                        user_agent=user_agent,
                    )
                    break
                except (aiohttp.ClientError, OSError, asyncio.TimeoutError):
                    if attempt == 3:
                        raise
                    await asyncio.sleep(2**attempt)
            page = data.get(collection) if isinstance(data, dict) else None
            if not isinstance(page, list) or any(
                not isinstance(record, dict) for record in page
            ):
                raise error.QueryError(f"invalid {collection} list response", query)
            pagination = data.get("pagination")
            if not isinstance(pagination, dict) or not isinstance(
                pagination.get("hasNextPage"), bool
            ):
                raise error.QueryError(
                    f"invalid {collection} pagination response", query
                )
            records.extend(page)
            if not pagination["hasNextPage"]:
                return records
            cursor = pagination.get("nextCursor")
            if not isinstance(cursor, str) or not cursor:
                raise error.QueryError(
                    f"{collection} pagination is missing a valid nextCursor", query
                )
            if cursor in seen_cursors:
                raise error.QueryError(
                    f"{collection} pagination repeated a cursor", query
                )
            seen_cursors.add(cursor)
            params = {"cursor": cursor}
