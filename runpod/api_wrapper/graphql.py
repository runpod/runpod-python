"""
RunPod | API Wrapper | GraphQL
"""

import json
from typing import Any, Dict

import requests


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
    return response.json()
