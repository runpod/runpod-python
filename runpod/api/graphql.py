"""
Runpod | API Wrapper | GraphQL
"""

import json
import os
from typing import Any, Dict, Optional

import requests

from runpod import error
from runpod.user_agent import USER_AGENT

HTTP_STATUS_UNAUTHORIZED = 401


def _graphql_url() -> str:
    api_url_base = os.environ.get("RUNPOD_API_BASE_URL", "https://api.runpod.io")
    return f"{api_url_base}/graphql"


def _resolve_api_key(api_key: Optional[str]) -> str:
    from runpod import api_key as global_api_key  # pylint: disable=import-outside-toplevel, cyclic-import

    effective_api_key = api_key or global_api_key
    if not effective_api_key:
        raise error.AuthenticationError("No API key provided")
    return effective_api_key


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

    response = requests.post(
        _graphql_url(),
        headers=_build_headers(effective_api_key),
        data=json.dumps(_build_payload(query, variables)),
        timeout=30,
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
    allow_anonymous: bool = False,
) -> Dict[str, Any]:
    """
    Async variant of run_graphql_query, sharing the same url, headers,
    auth resolution, and error handling.

    Args:
        query: The GraphQL query to execute.
        api_key: Optional API key to use for this query.
        variables: Optional GraphQL variables to send with the query.
        timeout: Total request timeout in seconds.
        allow_anonymous: Send the request without credentials when no
            key is available (pre-login flows).
    """
    import aiohttp  # pylint: disable=import-outside-toplevel

    if allow_anonymous:
        try:
            effective_api_key: Optional[str] = _resolve_api_key(api_key)
        except error.AuthenticationError:
            effective_api_key = None
    else:
        effective_api_key = _resolve_api_key(api_key)

    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        async with session.post(
            _graphql_url(),
            headers=_build_headers(effective_api_key),
            json=_build_payload(query, variables),
        ) as response:
            if response.status == HTTP_STATUS_UNAUTHORIZED:
                raise error.AuthenticationError(
                    "Unauthorized request, please check your API key."
                )
            response_json = await response.json()

    return _check_response(response_json, query)
