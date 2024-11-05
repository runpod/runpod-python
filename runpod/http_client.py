"""
HTTP Client abstractions
"""

import os

import requests
from aiohttp import ClientSession, ClientTimeout, TCPConnector, ClientResponseError

from .cli.groups.config.functions import get_credentials
from .tracer import create_aiohttp_tracer, create_request_tracer
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
    }


def AsyncClientSession(*args, **kwargs):  # pylint: disable=invalid-name
    """
    Deprecation from aiohttp.ClientSession forbids inheritance.
    This is now a factory method
    TODO: use httpx
    """
    return ClientSession(
        connector=TCPConnector(limit=0),
        headers=get_auth_header(),
        timeout=ClientTimeout(600, ceil_threshold=400),
        trace_configs=[create_aiohttp_tracer()],
        *args,
        **kwargs,
    )


class SyncClientSession(requests.Session):
    """
    Inherits requests.Session to override `request()` method for tracing
    TODO: use httpx
    """

    def request(self, method, url, **kwargs):  # pylint: disable=arguments-differ
        """
        Override for tracing. Not using super().request()
        to capture metrics for connection and transfer times
        """
        with create_request_tracer() as tracer:
            # Separate out the kwargs that are not applicable to `requests.Request`
            request_kwargs = {
                k: v
                for k, v in kwargs.items()
                # contains the names of the arguments
                if k in requests.Request.__init__.__code__.co_varnames
            }

            # Separate out the kwargs that are applicable to `requests.Request`
            send_kwargs = {k: v for k, v in kwargs.items() if k not in request_kwargs}

            # Create a PreparedRequest object to hold the request details
            req = requests.Request(method, url, **request_kwargs)
            prepped = self.prepare_request(req)
            tracer.request = prepped  # Assign the request to the tracer

            # Merge environment settings
            settings = self.merge_environment_settings(
                prepped.url,
                send_kwargs.get("proxies"),
                send_kwargs.get("stream"),
                send_kwargs.get("verify"),
                send_kwargs.get("cert"),
            )
            send_kwargs.update(settings)

            # Send the request
            response = self.send(prepped, **send_kwargs)
            tracer.response = response  # Assign the response to the tracer

            return response
