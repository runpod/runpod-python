"""
HTTP Client abstractions
"""

import os

import requests
from aiohttp import ClientSession, ClientTimeout, TCPConnector, ClientResponseError

from .cli.groups.config.functions import get_credentials
from .user_agent import USER_AGENT


class TooManyRequests(ClientResponseError):
    pass


def get_auth_header():
    """
    Produce a header dict with the `Authorization` key derived from
    credentials.get("api_key") OR os.getenv('RUNPOD_AI_API_KEY')
    """
    if credentials := get_credentials():
        auth = credentials.get("api_key", "")
    else:
        auth = os.getenv("RUNPOD_AI_API_KEY", "")

    return {
        "Content-Type": "application/json",
        "Authorization": auth,
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
    }


def AsyncClientSession(*args, **kwargs):  # pylint: disable=invalid-name
    """
    Deprecation from aiohttp.ClientSession forbids inheritance.
    This is now a factory method. Explicit session options override defaults.
    """
    if "connector" not in kwargs:
        kwargs["connector"] = TCPConnector(limit=0)
    if "headers" not in kwargs:
        kwargs["headers"] = get_auth_header()
    if "timeout" not in kwargs:
        kwargs["timeout"] = ClientTimeout(600, ceil_threshold=400)
    return ClientSession(*args, **kwargs)


class SyncClientSession(requests.Session):
    """
    Inherits requests.Session to override `request()` method for tracing
    """

    pass
