"""Runpod REST API transport."""

import asyncio
import json as json_module
import os
import time
from concurrent.futures import Future
from contextlib import nullcontext
from typing import Any, Mapping, Optional

import aiohttp
import requests

from runpod import error
from runpod.user_agent import USER_AGENT

from ._rate_limit import _RetryBudget, _validate_retry_options, rate_limits

HTTP_STATUS_NO_CONTENT = 204
HTTP_STATUS_BAD_REQUEST = 400
HTTP_STATUS_UNAUTHORIZED = 401
HTTP_STATUS_NOT_FOUND = 404


def _resolve_api_key(api_key: Optional[str]) -> str:
    from runpod import (  # pylint: disable=import-outside-toplevel,cyclic-import
        api_key as global_api_key,
    )

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


def _raise_for_error(
    status_code: int,
    method: str,
    path: str,
    text: str = "",
    retry_after: Optional[float] = None,
) -> None:
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
) -> Optional[dict[str, Any]]:
    """Send a REST request with bounded 429 retries and shared quota waits.

    ``timeout`` applies to each HTTP attempt. ``max_rate_limit_wait`` bounds
    cumulative quota sleeps in seconds; ``max_retries`` counts 429 retries.
    Network failures and other HTTP errors are not retried.
    """
    _validate_retry_options(max_retries, max_rate_limit_wait)
    key = _resolve_api_key(api_key)
    url = _build_url(path)
    headers = _build_headers(key)
    budget = _RetryBudget(max_retries, max_rate_limit_wait, f"{method.upper()} {path}")
    with rate_limits.bucket(url, key) as bucket:
        for attempt in range(max_retries + 1):
            while wait := bucket.acquire(budget):
                if isinstance(wait, Future):
                    wait.result()
                else:
                    time.sleep(wait)
            try:
                started_at = time.monotonic()
                response = requests.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json,
                    timeout=timeout,
                )
                try:
                    retry_after = bucket.update(
                        response.status_code,
                        response.headers,
                        attempt,
                        started_at=started_at,
                    )
                    if budget.can_retry(attempt, retry_after):
                        continue
                    if response.status_code >= HTTP_STATUS_BAD_REQUEST:
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
                    response.close()
            finally:
                bucket.release()


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
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[dict[str, Any]]:
    """Send a REST request with cancellable quota waits and bounded 429 retries.

    ``timeout`` applies to each HTTP attempt. ``max_rate_limit_wait`` bounds
    cumulative quota sleeps in seconds; ``max_retries`` counts 429 retries.
    Network failures and other HTTP errors are not retried.
    """
    _validate_retry_options(max_retries, max_rate_limit_wait)
    key = _resolve_api_key(api_key)
    url = _build_url(path)
    headers = _build_headers(key)
    budget = _RetryBudget(max_retries, max_rate_limit_wait, f"{method.upper()} {path}")
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    context = nullcontext(session) if session is not None else aiohttp.ClientSession()
    async with context as session:
        # aiohttp otherwise replays put/delete requests after an ambiguous disconnect.
        session._retry_connection = False  # pylint: disable=protected-access
        with rate_limits.bucket(url, key) as bucket:
            for attempt in range(max_retries + 1):
                while wait := bucket.acquire(budget):
                    if isinstance(wait, Future):
                        await asyncio.wrap_future(wait)
                    else:
                        await asyncio.sleep(wait)
                try:
                    started_at = time.monotonic()
                    async with session.request(
                        method,
                        url,
                        headers=headers,
                        params=params,
                        json=json,
                        timeout=client_timeout,
                    ) as response:
                        retry_after = bucket.update(
                            response.status,
                            response.headers,
                            attempt,
                            started_at=started_at,
                        )
                        if budget.can_retry(attempt, retry_after):
                            continue
                        if response.status >= HTTP_STATUS_BAD_REQUEST:
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


async def run_rest_paginated_request_async(
    path: str,
    collection: str,
    *,
    api_key: Optional[str] = None,
) -> list[dict[str, Any]]:
    """read all cursor pages with one session and four transport attempts per page."""
    records = []
    params = {}
    seen_cursors = set()
    query = f"GET {path}"
    async with aiohttp.ClientSession() as session:
        while True:
            for attempt in range(4):
                try:
                    data = await run_rest_request_async(
                        "GET", path, api_key=api_key, params=params, session=session
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
