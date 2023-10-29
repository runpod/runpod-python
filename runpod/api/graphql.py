"""
RunPod | API Wrapper | GraphQL
"""

import json
from typing import Any, Dict

import requests

from runpod import error

HTTP_STATUS_UNAUTHORIZED = 401

def run_graphql_query(query: str) -> Dict[str, Any]:
    '''
    Run a GraphQL query
    '''
    from runpod import api_key  # pylint: disable=import-outside-toplevel, cyclic-import
    url = f"https://api.runpod.io/graphql?api_key={api_key}"
    headers = {
        "Content-Type": "application/json",
    }
    data = json.dumps({"query": query})
    response = requests.post(url, headers=headers, data=data, timeout=30)

    if response.status_code == HTTP_STATUS_UNAUTHORIZED:
        raise error.AuthenticationError("Unauthorized request, please check your API key.")

    if "errors" in response.json():
        raise error.QueryError(
            response.json()["errors"][0]["message"],
            query
        )

    return response.json()
