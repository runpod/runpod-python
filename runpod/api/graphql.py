"""
Runpod | API Wrapper | GraphQL
"""

import json
from typing import Any, Dict, Optional

from runpod import error
from runpod.auth import resolve_api_key as _resolve_api_key
from runpod.user_agent import USER_AGENT

from .http import HTTPClient, SyncHTTPClient
from .routes import Route

HTTP_STATUS_UNAUTHORIZED = 401


def _build_headers(api_key: Optional[str]) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _build_payload(query: str, variables: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables
    return payload


def _check_response(response_json: Dict[str, Any], query: str) -> Dict[str, Any]:
    if "errors" in response_json:
        raise error.QueryError(response_json["errors"][0]["message"], query)
    return response_json


def run_graphql_query(
    query: str,
    api_key: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Run a GraphQL query with optional API key override.

    Args:
        query: The GraphQL query to execute.
        api_key: Optional API key to use for this query.
        variables: Optional GraphQL variables to send with the query.
    """
    effective_api_key = _resolve_api_key(api_key)

    with SyncHTTPClient().stream(
        Route("POST", "/graphql"),
        headers=_build_headers(effective_api_key),
        data=json.dumps(_build_payload(query, variables)),
        timeout=30,
    ) as response:
        if 300 <= response.status_code < 400:
            raise error.QueryError(
                "unexpected redirect", status_code=response.status_code
            )
        if response.status_code == HTTP_STATUS_UNAUTHORIZED:
            raise error.AuthenticationError(
                "Unauthorized request, please check your API key."
            )
        return _check_response(response.json(), query)


async def run_graphql_query_async(
    query: str,
    api_key: Optional[str] = None,
    variables: Optional[Dict[str, Any]] = None,
    timeout: float = 60.0,
    anonymous: bool = False,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Async variant of run_graphql_query, sharing the same url, headers,
    auth resolution, and error handling.

    Args:
        query: The GraphQL query to execute.
        api_key: Optional API key to use for this query.
        variables: Optional GraphQL variables to send with the query.
        timeout: Total request timeout in seconds.
        anonymous: Send the request without credentials even when a key
            is available. Login creates its auth request this way so a
            stale stored key cannot fail the request.
    """
    effective_api_key = None if anonymous else _resolve_api_key(api_key)

    async with HTTPClient().stream(
        Route("POST", "/graphql"),
        headers={**_build_headers(effective_api_key), **(headers or {})},
        json=_build_payload(query, variables),
        timeout=timeout,
    ) as response:
        if 300 <= response.status < 400:
            raise error.QueryError("unexpected redirect", status_code=response.status)
        if response.status == HTTP_STATUS_UNAUTHORIZED:
            raise error.AuthenticationError(
                "Unauthorized request, please check your API key."
            )
        response_json = await response.json()

    return _check_response(response_json, query)
