"""
PodWorker | serverless | utils | rp_ssrf.py

SSRF-safe fetching of user-supplied (job-input) URLs.

Job input carries arbitrary URLs that the worker downloads. Without restriction
a job can point the worker at cloud instance-metadata (169.254.169.254) or other
non-public addresses (CWE-918). This module blocks any destination that is not a
globally-routable IP, pins connections to a pre-validated address to defeat DNS
rebinding, re-validates redirect hops, and caps download size.
"""

import ipaddress
import os
import socket
from contextlib import suppress
from typing import Iterator, List, Optional
from urllib.parse import urljoin, urlparse

from requests.adapters import HTTPAdapter

from runpod.http_client import SyncClientSession

_ALLOWED_SCHEMES = ("http", "https")
_REDIRECT_STATUS = frozenset((301, 302, 303, 307, 308))
_DEFAULT_MAX_REDIRECTS = 5

# Size cap. Generous default that does not break legitimate image/zip/model
# downloads but bounds pathological abuse and the in-memory read in file().
_MAX_BYTES_ENV = "RUNPOD_MAX_DOWNLOAD_BYTES"
_DEFAULT_MAX_BYTES = 5 * 1024 ** 3  # 5 GiB

# CGNAT / shared address space (RFC 6598). Not reported by IPv4Address.is_private
# before CPython 3.12.4, so it is blocked explicitly rather than relied upon.
_EXTRA_BLOCKED_NETWORKS = [ipaddress.ip_network("100.64.0.0/10")]

# Escape hatch for workers that legitimately fetch from a same-VPC / self-hosted
# endpoint. Off by default; scheme allowlist and size cap still apply when set.
_ALLOW_PRIVATE_ENV = "RUNPOD_ALLOW_PRIVATE_DOWNLOAD_URLS"


class SSRFError(ValueError):
    """
    Raised when a URL is rejected as unsafe (non-public destination, blocked
    scheme, or oversized body).

    Subclasses ValueError and deliberately NOT requests.RequestException so it
    bypasses the download path's backoff retry and `except RequestException`
    handler: a blocked internal URL must fail loudly, not be retried or silently
    reported as an ordinary failed download.
    """


def _ssrf_protection_enabled() -> bool:
    return os.environ.get(_ALLOW_PRIVATE_ENV, "").strip().lower() not in (
        "1",
        "true",
        "yes",
    )


def is_safe_address(ip: str) -> bool:
    """
    Return True only for a globally-routable unicast IP address.

    Blocks loopback, link-local (incl. 169.254.169.254 metadata), RFC1918,
    CGNAT, reserved, multicast, unspecified, IPv6 unique-local/link-local, and
    IPv4-mapped IPv6 forms of any of the above. Unparseable input is unsafe.
    """
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False

    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:10.0.0.1) and classify the real IPv4.
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped

    if any(address in network for network in _EXTRA_BLOCKED_NETWORKS):
        return False

    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        return False

    return address.is_global


def resolve_and_validate(host: str, port: int) -> List[str]:
    """
    Resolve `host` and return its IPs, failing closed on any unsafe address.

    Raises SSRFError if the host cannot be resolved, resolves to nothing, or
    resolves to any non-global address (a host resolving to both a public and a
    private IP is treated as hostile). Validation is skipped when the escape
    hatch env var is set, but resolution still occurs so callers can pin.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as err:
        raise SSRFError(f"could not resolve host {host!r}: {err}") from err

    ips: List[str] = []
    for info in infos:
        ip = info[4][0]
        if ip not in ips:
            ips.append(ip)

    if not ips:
        raise SSRFError(f"no addresses resolved for host {host!r}")

    if _ssrf_protection_enabled():
        for ip in ips:
            if not is_safe_address(ip):
                raise SSRFError(
                    f"host {host!r} resolves to non-public address {ip}"
                )

    return ips


def max_download_bytes() -> int:
    """Configured per-download byte cap; defaults to 5 GiB."""
    raw = os.environ.get(_MAX_BYTES_ENV)
    if raw is None:
        return _DEFAULT_MAX_BYTES
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_BYTES
    return value if value > 0 else _DEFAULT_MAX_BYTES


class PinnedIPAdapter(HTTPAdapter):
    """
    Requests adapter that dials a pre-validated IP instead of re-resolving the
    URL host, defeating DNS rebinding.

    The superclass builds the connection pool with its normal TLS/verification
    configuration; this adapter only redirects the socket to the pinned IP and
    keeps the TLS SNI, certificate hostname check, and Host header bound to the
    original URL host. It never alters certificate verification.
    """

    def __init__(self, pinned_ip: str, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(**kwargs)

    def send(self, request, **kwargs):
        parsed = urlparse(request.url)
        authority = parsed.hostname or ""
        if parsed.port is not None:
            authority = f"{authority}:{parsed.port}"
        request.headers["Host"] = authority
        return super().send(request, **kwargs)

    def _pin(self, pool, url: str):
        parsed = urlparse(url)
        if parsed.scheme == "https":
            pool.assert_hostname = parsed.hostname
            pool.conn_kw = dict(pool.conn_kw or {})
            pool.conn_kw["server_hostname"] = parsed.hostname
        pool.host = self._pinned_ip
        return pool

    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        pool = super().get_connection_with_tls_context(
            request, verify, proxies=proxies, cert=cert
        )
        return self._pin(pool, request.url)

    def get_connection(self, url, proxies=None):  # requests < 2.32 fallback
        pool = super().get_connection(url, proxies=proxies)
        return self._pin(pool, url)


def _build_pinned_session(pinned_ip: str) -> SyncClientSession:
    """A session whose http(s) connections dial only `pinned_ip`."""
    session = SyncClientSession()
    adapter = PinnedIPAdapter(pinned_ip)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _close_session_quietly(session: SyncClientSession) -> None:
    """
    Close a pinned session best-effort, tolerating teardown errors.

    This runs only on cleanup paths (a failed request, a closing response) where
    the caller's original exception is the meaningful one. A failure while
    closing the session's connection pools must not mask that exception, so it is
    suppressed rather than raised. Session.close() is idempotent and does no
    actionable I/O, so nothing worth surfacing is lost.
    """
    with suppress(Exception):
        session.close()


def _bind_session_to_response(response, session: SyncClientSession) -> None:
    """
    Tie `session` cleanup to `response.close()`.

    Each safe_get() call owns a single-use session; the caller only holds the
    response. Closing a requests.Response releases its connection but not the
    session (its adapters and connection pools), so without this the session
    leaks one set of pooled sockets/FDs per download in a long-lived worker.
    Wrapping close() makes `with safe_get(...) as r:` (and any direct
    response.close()) tear down both.
    """
    original_close = response.close

    def close_both(*args, **kwargs):
        try:
            original_close(*args, **kwargs)
        finally:
            _close_session_quietly(session)

    response.close = close_both


def _enforce_content_length(response, max_bytes: Optional[int]) -> None:
    if max_bytes is None:
        return
    declared = response.headers.get("Content-Length")
    if declared is None:
        return
    try:
        size = int(declared)
    except ValueError:
        return
    if size > max_bytes:
        response.close()
        raise SSRFError(f"download exceeds size cap: {size} > {max_bytes} bytes")


def iter_content_capped(response, chunk_size: int, max_bytes: Optional[int]) -> Iterator[bytes]:
    """
    Yield the response body in chunks, aborting with SSRFError if the running
    total exceeds `max_bytes`. Guards against a lying/absent Content-Length.
    """
    total = 0
    for chunk in response.iter_content(chunk_size=chunk_size):
        if not chunk:
            continue
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            response.close()
            raise SSRFError(f"download exceeds size cap: >{max_bytes} bytes")
        yield chunk


def safe_get(
    url: str,
    *,
    stream: bool = True,
    timeout: int = 30,
    headers: Optional[dict] = None,
    max_bytes: Optional[int] = None,
    max_redirects: int = _DEFAULT_MAX_REDIRECTS,
):
    """
    Fetch a user-supplied URL with SSRF protections.

    Enforces an http(s) scheme allowlist, resolves and validates the host to a
    public IP, pins the connection to that IP, and re-validates every redirect
    hop. Rejects a body whose declared size exceeds the cap. Returns the
    (streamed) requests.Response for the caller to consume; use
    iter_content_capped to enforce the cap while reading. Raises SSRFError on
    any block.
    """
    if max_bytes is None:
        max_bytes = max_download_bytes()

    current = url
    for _ in range(max_redirects + 1):
        parsed = urlparse(current)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise SSRFError(f"blocked URL scheme {parsed.scheme!r}: {current}")
        if not parsed.hostname:
            raise SSRFError(f"URL has no host: {current}")

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        ips = resolve_and_validate(parsed.hostname, port)

        session = _build_pinned_session(ips[0])
        try:
            response = session.get(
                current,
                headers=headers,
                allow_redirects=False,
                stream=stream,
                timeout=timeout,
            )
        except BaseException:
            _close_session_quietly(session)
            raise

        if response.status_code in _REDIRECT_STATUS:
            location = response.headers.get("Location")
            try:
                response.close()
            finally:
                _close_session_quietly(session)
            if not location:
                raise SSRFError(f"redirect without Location header: {current}")
            current = urljoin(current, location)
            continue

        # Bind before the cap check so a Content-Length breach (which closes
        # the response) also tears down the session.
        _bind_session_to_response(response, session)
        _enforce_content_length(response, max_bytes)
        return response

    raise SSRFError(f"exceeded maximum redirects ({max_redirects}): {url}")
