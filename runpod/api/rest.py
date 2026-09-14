"""Runpod REST API transport."""

import os
from typing import Any, Mapping, Optional

import requests

from runpod import error
from runpod.user_agent import USER_AGENT

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


def _build_url(path: str, base_url: Optional[str] = None) -> str:
    api_url_base = base_url or os.environ.get(
        "RUNPOD_API_BASE_URL", "https://api.runpod.io"
    )
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


def _raise_for_status(
    status_code: int,
    payload: Mapping[str, Any],
    text: str,
    method: str,
    path: str,
) -> None:
    """Map HTTP error details consistently across REST transports."""
    if status_code == HTTP_STATUS_UNAUTHORIZED:
        raise error.AuthenticationError(
            "Unauthorized request, please check your API key."
        )

    if status_code < HTTP_STATUS_BAD_REQUEST:
        return

    message = payload.get("detail") or payload.get("title")
    if not message:
        message = text or f"Request failed with status {status_code}"

    raise error.QueryError(
        str(message),
        f"{method.upper()} {path}",
        status_code=status_code,
        errors=payload.get("errors"),
    )


def _raise_for_error(response: requests.Response, method: str, path: str) -> None:
    if response.status_code < HTTP_STATUS_BAD_REQUEST:
        return
    if response.status_code == HTTP_STATUS_UNAUTHORIZED:
        _raise_for_status(response.status_code, {}, "", method, path)
    _raise_for_status(
        response.status_code, _response_json(response), response.text, method, path
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
