"""Runpod REST API transport."""

import asyncio
import json as json_module
import os
import time
from typing import Any, Mapping, Optional

import aiohttp
import requests

from runpod import error
from runpod.user_agent import USER_AGENT

from ._rate_limit import _validate_retry_options, rate_limits

HTTP_STATUS_NO_CONTENT = 204
HTTP_STATUS_BAD_REQUEST = 400
HTTP_STATUS_UNAUTHORIZED = 401
HTTP_STATUS_NOT_FOUND = 404


def _resolve_api_key(api_key: Optional[str]) -> str:
    from runpod import (
        api_key as global_api_key,
    )  # pylint: disable=import-outside-toplevel,cyclic-import

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
    with rate_limits.bucket(url, key) as bucket:
        for attempt in range(max_retries + 1):
            while delay := bucket.acquire():
                if delay > max_rate_limit_wait:
                    raise error.RateLimitError(
                        "Rate limit wait budget exceeded",
                        f"{method.upper()} {path}",
                        retry_after=delay,
                    )
                time.sleep(delay)
                max_rate_limit_wait -= delay
            try:
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
                        response.status_code, response.headers, attempt
                    )
                    if (
                        response.status_code == 429
                        and attempt < max_retries
                        and retry_after <= max_rate_limit_wait
                    ):
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
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        # aiohttp otherwise replays put/delete requests after an ambiguous disconnect.
        session._retry_connection = False  # pylint: disable=protected-access
        with rate_limits.bucket(url, key) as bucket:
            for attempt in range(max_retries + 1):
                while delay := bucket.acquire():
                    if delay > max_rate_limit_wait:
                        raise error.RateLimitError(
                            "Rate limit wait budget exceeded",
                            f"{method.upper()} {path}",
                            retry_after=delay,
                        )
                    await asyncio.sleep(delay)
                    max_rate_limit_wait -= delay
                try:
                    async with session.request(
                        method, url, headers=headers, params=params, json=json
                    ) as response:
                        retry_after = bucket.update(
                            response.status, response.headers, attempt
                        )
                        if (
                            response.status == 429
                            and attempt < max_retries
                            and retry_after <= max_rate_limit_wait
                        ):
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
