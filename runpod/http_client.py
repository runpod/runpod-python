"""
HTTP Client abstractions
"""

import os

import requests
from aiohttp import ClientSession, ClientTimeout, TCPConnector

from .cli.groups.config.functions import get_credentials
from .tracer import create_aiohttp_tracer, create_request_tracer
from .user_agent import USER_AGENT


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


def AsyncClientSession(*args, **kwargs):
    """
    Deprecation from aiohttp.ClientSession forbids inheritance.
    This is now a factory method
    TODO: use httpx
    """
    connector = TCPConnector(
        # Total number of simultaneous connections
        limit=100,
        # Limit connections per host to avoid overwhelming a single server
        limit_per_host=10,
        # Enable DNS caching to reduce DNS lookup overhead
        use_dns_cache=True,
        # Cache DNS entries for 10 seconds
        ttl_dns_cache=10,
        # Keep connections alive for 15 seconds
        keepalive_timeout=15,
        # Do not force close connections after each request
        force_close=False,
        # Ceiling threshold to avoid last-second timeouts
        timeout_ceil_threshold=5,
    )

    timeout = ClientTimeout(
        # Allow a slightly longer total timeout
        total=80,
        # Time to establish a connection
        connect=10,
        # Time to wait for a response from the server
        sock_read=30,
        # Time to open a connection to a socket
        sock_connect=10,
        # Buffer to avoid timing out at the very last second
        ceil_threshold=5,
    )

    return ClientSession(
        connector=connector,
        timeout=timeout,
        headers=get_auth_header(),
        trace_configs=[create_aiohttp_tracer()],
        *args,
        **kwargs,
    )


class SyncClientSession(requests.Session):
    """
    Inherits requests.Session to override `request()` method for tracing
    TODO: use httpx
    """

    def request(self, method, url, **kwargs):
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
