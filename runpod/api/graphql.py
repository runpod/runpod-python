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


def run_graphql_query(query: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Run a GraphQL query with optional API key override.
    
    Args:
        query: The GraphQL query to execute.
        api_key: Optional API key to use for this query.
    """
    from runpod import api_key as global_api_key  # pylint: disable=import-outside-toplevel, cyclic-import
    
    # Use provided API key or fall back to global
    effective_api_key = api_key or global_api_key
    
    if not effective_api_key:
        raise error.AuthenticationError("No API key provided")

    api_url_base = os.environ.get("RUNPOD_API_BASE_URL", "https://api.runpod.io")
    url = f"{api_url_base}/graphql"

    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {effective_api_key}",
    }

    data = json.dumps({"query": query})
    response = requests.post(url, headers=headers, data=data, timeout=30)

    if response.status_code == HTTP_STATUS_UNAUTHORIZED:
        raise error.AuthenticationError(
            "Unauthorized request, please check your API key."
        )

    if "errors" in response.json():
        raise error.QueryError(response.json()["errors"][0]["message"], query)

    return response.json()
