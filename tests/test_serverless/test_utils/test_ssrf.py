"""Tests for runpod | serverless | utils | rp_ssrf.py"""

import http.server
import os
import socket
import threading
import unittest
from unittest.mock import MagicMock, patch

from requests import RequestException

from runpod.serverless.utils.rp_ssrf import (
    SSRFError,
    _build_pinned_session,
    is_safe_address,
    iter_content_capped,
    max_download_bytes,
    resolve_and_validate,
    safe_get,
)


class _FakeResponse:
    """Minimal stand-in for requests.Response used by safe_get."""

    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def close(self):
        self.closed = True


def _addrinfo(*ips):
    """Build a getaddrinfo-style return value for the given IPs."""
    infos = []
    for ip in ips:
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        sockaddr = (ip, 443, 0, 0) if family == socket.AF_INET6 else (ip, 443)
        infos.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
    return infos


class TestIsSafeAddress(unittest.TestCase):
    """Address classifier: only globally-routable IPs are safe."""

    def test_blocks_non_global_and_allows_public(self):
        blocked = [
            "169.254.169.254",  # cloud metadata (link-local)
            "169.254.0.1",  # link-local
            "127.0.0.1",  # loopback
            "0.0.0.0",  # unspecified
            "10.0.0.5",  # RFC1918
            "172.16.0.1",  # RFC1918
            "172.31.255.255",  # RFC1918
            "192.168.1.1",  # RFC1918
            "100.64.0.1",  # CGNAT / shared address space
            "224.0.0.1",  # multicast
            "240.0.0.1",  # reserved
            "::1",  # IPv6 loopback
            "fe80::1",  # IPv6 link-local
            "fc00::1",  # IPv6 unique-local
            "ff02::1",  # IPv6 multicast
            "::",  # IPv6 unspecified
            "::ffff:10.0.0.1",  # IPv4-mapped IPv6 of a private addr
            "::ffff:169.254.169.254",  # IPv4-mapped metadata
            "not-an-ip",  # unparseable
        ]
        allowed = [
            "8.8.8.8",
            "1.1.1.1",
            "93.184.216.34",  # example.com
            "2606:2800:220:1:248:1893:25c8:1946",  # public IPv6
        ]

        for ip in blocked:
            self.assertFalse(is_safe_address(ip), f"{ip} should be blocked")
        for ip in allowed:
            self.assertTrue(is_safe_address(ip), f"{ip} should be allowed")


class TestResolveAndValidate(unittest.TestCase):
    """DNS resolution fails closed on any non-global address."""

    def test_ssrf_error_is_not_a_request_exception(self):
        # Blocks must bypass the caller's `except RequestException` + backoff.
        self.assertTrue(issubclass(SSRFError, ValueError))
        self.assertFalse(issubclass(SSRFError, RequestException))

    @patch("runpod.serverless.utils.rp_ssrf.socket.getaddrinfo")
    def test_returns_ips_when_all_public(self, mock_gai):
        mock_gai.return_value = _addrinfo("8.8.8.8", "1.1.1.1")
        self.assertEqual(resolve_and_validate("example.com", 443), ["8.8.8.8", "1.1.1.1"])

    @patch("runpod.serverless.utils.rp_ssrf.socket.getaddrinfo")
    def test_raises_on_private(self, mock_gai):
        mock_gai.return_value = _addrinfo("169.254.169.254")
        with self.assertRaises(SSRFError):
            resolve_and_validate("metadata.attacker.test", 80)

    @patch("runpod.serverless.utils.rp_ssrf.socket.getaddrinfo")
    def test_fails_closed_on_mixed_public_and_private(self, mock_gai):
        # A host resolving to both a public and a private IP is hostile.
        mock_gai.return_value = _addrinfo("8.8.8.8", "10.0.0.5")
        with self.assertRaises(SSRFError):
            resolve_and_validate("rebind.attacker.test", 443)

    @patch("runpod.serverless.utils.rp_ssrf.socket.getaddrinfo")
    def test_raises_when_dns_fails(self, mock_gai):
        mock_gai.side_effect = socket.gaierror("name resolution failed")
        with self.assertRaises(SSRFError):
            resolve_and_validate("nonexistent.attacker.test", 443)

    @patch.dict(os.environ, {"RUNPOD_ALLOW_PRIVATE_DOWNLOAD_URLS": "true"})
    @patch("runpod.serverless.utils.rp_ssrf.socket.getaddrinfo")
    def test_escape_hatch_allows_private(self, mock_gai):
        mock_gai.return_value = _addrinfo("10.0.0.5")
        self.assertEqual(resolve_and_validate("internal.vpc.test", 80), ["10.0.0.5"])


class TestSafeGet(unittest.TestCase):
    """Scheme allowlist, redirect re-validation, and size-cap enforcement."""

    def test_rejects_non_http_schemes(self):
        for bad in ["file:///etc/passwd", "gopher://h/x", "ftp://h/f", "data:text/plain,x"]:
            with self.assertRaises(SSRFError):
                safe_get(bad)

    @patch("runpod.serverless.utils.rp_ssrf._build_pinned_session")
    @patch("runpod.serverless.utils.rp_ssrf.resolve_and_validate")
    def test_redirect_to_metadata_is_blocked(self, mock_resolve, mock_session):
        def resolve(host, _port):
            if host == "example.com":
                return ["93.184.216.34"]
            raise SSRFError(f"blocked {host}")

        mock_resolve.side_effect = resolve
        session = MagicMock()
        session.get.return_value = _FakeResponse(
            302, {"Location": "http://169.254.169.254/latest/meta-data/"}
        )
        mock_session.return_value = session

        with self.assertRaises(SSRFError):
            safe_get("https://example.com/image.jpg")

    @patch("runpod.serverless.utils.rp_ssrf._build_pinned_session")
    @patch("runpod.serverless.utils.rp_ssrf.resolve_and_validate", return_value=["93.184.216.34"])
    def test_too_many_redirects(self, _mock_resolve, mock_session):
        session = MagicMock()
        session.get.return_value = _FakeResponse(
            302, {"Location": "https://example.com/loop"}
        )
        mock_session.return_value = session

        with self.assertRaises(SSRFError):
            safe_get("https://example.com/start", max_redirects=3)

    @patch("runpod.serverless.utils.rp_ssrf._build_pinned_session")
    @patch("runpod.serverless.utils.rp_ssrf.resolve_and_validate", return_value=["93.184.216.34"])
    def test_content_length_over_cap_rejected(self, _mock_resolve, mock_session):
        session = MagicMock()
        session.get.return_value = _FakeResponse(200, {"Content-Length": str(10 * 1024 * 1024)})
        mock_session.return_value = session

        with self.assertRaises(SSRFError):
            safe_get("https://example.com/big.bin", max_bytes=1024)

    @patch("runpod.serverless.utils.rp_ssrf._build_pinned_session")
    @patch("runpod.serverless.utils.rp_ssrf.resolve_and_validate", return_value=["93.184.216.34"])
    def test_returns_response_on_success(self, _mock_resolve, mock_session):
        response = _FakeResponse(200, {"Content-Length": "500"})
        session = MagicMock()
        session.get.return_value = response
        mock_session.return_value = session

        self.assertIs(safe_get("https://example.com/ok.jpg", max_bytes=4096), response)


class TestIterContentCapped(unittest.TestCase):
    """Streaming byte counter aborts past the cap even when headers lie."""

    def test_aborts_when_stream_exceeds_cap(self):
        response = MagicMock()
        response.iter_content.return_value = [b"x" * 100, b"y" * 100]
        with self.assertRaises(SSRFError):
            list(iter_content_capped(response, chunk_size=100, max_bytes=150))

    def test_passes_through_under_cap(self):
        response = MagicMock()
        response.iter_content.return_value = [b"x" * 50, b"y" * 50]
        self.assertEqual(
            b"".join(iter_content_capped(response, chunk_size=100, max_bytes=1000)),
            b"x" * 50 + b"y" * 50,
        )


class TestMaxDownloadBytes(unittest.TestCase):
    def test_default_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RUNPOD_MAX_DOWNLOAD_BYTES", None)
            self.assertEqual(max_download_bytes(), 5 * 1024 ** 3)

    @patch.dict(os.environ, {"RUNPOD_MAX_DOWNLOAD_BYTES": "12345"})
    def test_env_override(self):
        self.assertEqual(max_download_bytes(), 12345)


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    received_host = None

    def do_GET(self):  # noqa: N802
        type(self).received_host = self.headers.get("Host")
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence test server logging
        pass


class TestPinnedIPAdapter(unittest.TestCase):
    """The socket must dial the pinned IP while the Host header keeps the URL host."""

    def test_dials_pinned_ip_and_preserves_host_header(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), _RecordingHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # URL host is example.com (won't route to our server); pin to loopback.
            session = _build_pinned_session("127.0.0.1")
            response = session.get(f"http://example.com:{port}/path", timeout=5)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b"ok")
            self.assertEqual(_RecordingHandler.received_host, f"example.com:{port}")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
