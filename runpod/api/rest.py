"""Runpod REST API transport."""

import json as json_module
import os
from typing import Any, Mapping, Optional

import aiohttp
import requests

from runpod import error
from runpod.user_agent import USER_AGENT

HTTP_STATUS_NO_CONTENT = 204
HTTP_STATUS_BAD_REQUEST = 400
HTTP_STATUS_UNAUTHORIZED = 401
HTTP_STATUS_NOT_FOUND = 404


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


def _raise_for_error(
    status_code: int, method: str, path: str, text: str = ""
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
    if response.status_code >= HTTP_STATUS_BAD_REQUEST:
        _raise_for_error(response.status_code, method, path, response.text)

    if response.status_code == HTTP_STATUS_NO_CONTENT or not response.content:
        return None
    return response.json()


async def run_rest_request_async(
    method: str,
    path: str,
    *,
    api_key: Optional[str] = None,
    params: Optional[Mapping[str, Any]] = None,
    json: Optional[Mapping[str, Any]] = None,
    timeout: float = 30,
) -> Optional[dict[str, Any]]:
    """Send an authenticated REST request without blocking the event loop."""
    headers = _build_headers(_resolve_api_key(api_key))
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        # aiohttp otherwise replays put/delete requests after an ambiguous disconnect.
        session._retry_connection = False  # pylint: disable=protected-access
        async with session.request(
            method,
            _build_url(path),
            headers=headers,
            params=params,
            json=json,
        ) as response:
            if response.status >= HTTP_STATUS_BAD_REQUEST:
                text = (
                    ""
                    if response.status == HTTP_STATUS_UNAUTHORIZED
                    else await response.text(errors="replace")
                )
                _raise_for_error(response.status, method, path, text)

            if response.status == HTTP_STATUS_NO_CONTENT or not await response.read():
                return None
            return await response.json(content_type=None)
