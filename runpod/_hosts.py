"""Service hosts used to build console, proxy, and endpoint URLs.

Each host can be overridden with an environment variable, which is read on
every call so a change takes effect without re-importing ``runpod``. Unset or
blank values fall back to the public Runpod hosts.

    RUNPOD_CONSOLE_URL        web console base URL (default https://console.runpod.io)
    RUNPOD_PROXY_DOMAIN       pod HTTP proxy domain (default proxy.runpod.net)
    RUNPOD_ENDPOINT_BASE_URL  serverless base URL (default https://api.runpod.ai/v2)

The endpoint domain is the host of ``RUNPOD_ENDPOINT_BASE_URL``. When that is
unset or blank, a non-blank ``runpod.endpoint_url_base`` set programmatically
is used instead.

Example:
    >>> proxy_url("abc123", 8080)
    'https://abc123-8080.proxy.runpod.net'
"""

import os
from typing import Union

DEFAULT_CONSOLE_URL = "https://console.runpod.io"
DEFAULT_PROXY_DOMAIN = "proxy.runpod.net"
DEFAULT_ENDPOINT_BASE_URL = "https://api.runpod.ai/v2"


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def console_url() -> str:
    """Web console base URL, without a trailing slash."""
    return _env("RUNPOD_CONSOLE_URL", DEFAULT_CONSOLE_URL).rstrip("/")


def proxy_url(pod_id: str, port: Union[int, str]) -> str:
    """Public HTTP proxy URL for ``port`` on ``pod_id``."""
    domain = _env("RUNPOD_PROXY_DOMAIN", DEFAULT_PROXY_DOMAIN).strip("/")
    return f"https://{pod_id}-{port}.{domain}"


def endpoint_domain() -> str:
    """Host of the serverless data plane, e.g. ``api.runpod.ai``.

    Load-balanced endpoints are served on ``<endpoint_id>.<endpoint_domain>``.
    """
    import runpod

    module_base = (runpod.endpoint_url_base or "").strip()
    base = _env("RUNPOD_ENDPOINT_BASE_URL", module_base or DEFAULT_ENDPOINT_BASE_URL)
    host = base.split("://", 1)[-1]
    return host.split("/", 1)[0]
