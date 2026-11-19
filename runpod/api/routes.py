"""route templates and service origins."""

import re
from urllib.parse import quote, urlsplit, urlunsplit

from runpod._hosts import (
    api_base_url as api_base_url,
    endpoint_base_url as _endpoint_base_url,
    host_api_base_url as host_api_base_url,
    proxy_url,
)


def endpoint_base_url(endpoint_id: str | None = None) -> str:
    base = _endpoint_base_url()
    if endpoint_id is None:
        return base
    return Route("GET", "/{endpoint_id}", endpoint_id=endpoint_id).url(base)


def _host_label(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid resource id for a service hostname")
    return value


def load_balancer_base_url(endpoint_id: str) -> str:
    base = urlsplit(endpoint_base_url())
    if base.scheme not in {"http", "https"} or not base.netloc or base.username:
        raise ValueError("invalid endpoint base URL")
    return urlunsplit(
        (base.scheme, f"{_host_label(endpoint_id)}.{base.netloc}", "", "", "")
    )


def pod_proxy_base_url(pod_id: str, port: int) -> str:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("invalid pod proxy port")
    return proxy_url(_host_label(pod_id), port)


class Route:
    """an HTTP method and a path template with escaped parameters."""

    def __init__(self, method: str, path: str, **parameters):
        self.method = method.upper()
        self.path = path
        self.parameters = parameters

    @property
    def resolved_path(self) -> str:
        if not self.parameters:
            return self.path
        parameters = {}
        for name, value in self.parameters.items():
            encoded = quote(str(value), safe="")
            parameters[name] = (
                encoded.replace(".", "%2E") if encoded in {".", ".."} else encoded
            )
        return self.path.format_map(parameters)

    def url(self, base_url: str | None = None) -> str:
        base_url = api_base_url() if base_url is None else base_url
        base = urlsplit(base_url)
        if base.scheme not in {"http", "https"} or not base.netloc or base.username:
            raise ValueError(
                "base URL must be an HTTP origin without embedded credentials"
            )
        path = self.resolved_path
        if not path:
            return base_url
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment or path.startswith("//"):
            raise ValueError("route paths must be relative to their service")
        if any(part in {".", ".."} for part in parsed.path.split("/")):
            raise ValueError("route paths must not traverse parent directories")
        if base.query or base.fragment:
            raise ValueError("a service base URL cannot contain a query or fragment")
        resolved = quote(
            f"{base.path.rstrip('/')}/{parsed.path.lstrip('/')}",
            safe="/%:@!$&'()*+,;=-._~",
        )
        query = quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
        return urlunsplit((base.scheme, base.netloc, resolved, query, ""))
