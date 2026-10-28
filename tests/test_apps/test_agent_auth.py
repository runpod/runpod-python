"""email sign-in transport and proof-of-possession tests."""

import asyncio
import base64
import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import aiohttp
import pytest

import runpod
from runpod.apps.auth import LoginError, _post_authentication_attempt, agent_login
from runpod.user_agent import USER_AGENT


@pytest.fixture
def signup_server(monkeypatch, tmp_path):
    state = {
        "requests": [],
        "responses": [
            (201, {"attemptId": "att_test"}),
            (200, {"credential": {"id": "key_test", "apiKey": "rpa_test"}}),
        ],
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.path, dict(self.headers), body))
            status, payload = state["responses"].pop(0)
            response = (
                payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.send_header("Location", "/redirect-target")
            self.send_header("Set-Cookie", "browser_session=unused")
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "RUNPOD_API_BASE_URL", f"http://127.0.0.1:{server.server_port}/prefix/"
    )
    monkeypatch.setenv("RUNPOD_API_KEY", "existing-env-key")
    monkeypatch.setattr(runpod, "api_key", "existing-global-key")
    netrc = tmp_path / "netrc"
    netrc.write_text(
        "machine 127.0.0.1 login existing-user password existing-password\n"
    )
    monkeypatch.setenv("NETRC", str(netrc))
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_agent_login_uses_anonymous_pkce_exchange(signup_server):
    def read_code():
        assert len(signup_server["requests"]) == 1
        return "012345"

    assert (
        asyncio.run(agent_login("owner@example.com", read_code=read_code)) == "rpa_test"
    )
    start, verify = signup_server["requests"]
    assert start[0] == "/prefix/v2/signup/authentication-attempts"
    assert verify[0] == "/prefix/v2/signup/authentication-attempts/att_test/verify"
    assert UUID(start[1]["Idempotency-Key"]).version == 4
    assert "Idempotency-Key" not in verify[1]
    assert set(start[2]) == {"email", "keyName", "codeChallenge"}
    assert start[2]["email"] == "owner@example.com"
    assert start[2]["keyName"] == "runpod-cli-agent"
    assert set(verify[2]) == {"code", "codeVerifier"}
    assert verify[2]["code"] == "012345"
    verifier = verify[2]["codeVerifier"]
    assert re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    assert start[2]["codeChallenge"] == challenge.decode().rstrip("=")
    assert verifier not in json.dumps(start)
    for path, headers, _ in (start, verify):
        assert "Authorization" not in headers
        assert "Cookie" not in headers
        assert headers["User-Agent"] == USER_AGENT
        assert verifier not in path


def test_each_login_has_its_own_verifier_and_idempotency_key(signup_server):
    signup_server["responses"] *= 2
    for _ in range(2):
        asyncio.run(agent_login("owner@example.com", read_code=lambda: "123456"))
    first, _, second, _ = signup_server["requests"]
    assert first[1]["Idempotency-Key"] != second[1]["Idempotency-Key"]
    assert first[2]["codeChallenge"] != second[2]["codeChallenge"]


@pytest.mark.parametrize(
    "status, message",
    [
        (403, "console"),
        (404, "unavailable"),
        (422, "check --email"),
        (429, "wait"),
        (500, "HTTP 500"),
        (307, "HTTP 307"),
    ],
)
def test_start_errors_do_not_prompt_or_follow_redirects(signup_server, status, message):
    signup_server["responses"] = [(status, {"detail": "sensitive-server-detail"})]
    read_code = MagicMock()
    with pytest.raises(LoginError, match=message) as exc:
        asyncio.run(agent_login("owner@example.com", read_code=read_code))
    read_code.assert_not_called()
    assert len(signup_server["requests"]) == 1
    assert "sensitive-server-detail" not in str(exc.value)


@pytest.mark.parametrize(
    "status, message",
    [
        (403, "console"),
        (404, "expired"),
        (429, "wait"),
        (503, "HTTP 503"),
        (307, "HTTP 307"),
    ],
)
def test_verification_errors_are_not_retried(signup_server, status, message):
    signup_server["responses"][1] = (status, {"detail": "sensitive-server-detail"})
    with pytest.raises(LoginError, match=message) as exc:
        asyncio.run(agent_login("owner@example.com", read_code=lambda: "123456"))
    assert len(signup_server["requests"]) == 2
    assert "sensitive-server-detail" not in str(exc.value)


@pytest.mark.parametrize("rejections", [1, 4])
def test_rejected_codes_reuse_attempt_and_verifier(signup_server, rejections):
    signup_server["responses"][1:1] = [
        (422, {"detail": "sensitive-server-detail"})
    ] * rejections
    codes = [f"{number:06d}" for number in range(rejections + 1)]
    read_code = MagicMock(side_effect=codes)

    assert (
        asyncio.run(agent_login("owner@example.com", read_code=read_code)) == "rpa_test"
    )
    start, *verifications = signup_server["requests"]
    assert read_code.call_count == rejections + 1
    assert len(verifications) == rejections + 1
    assert start[0] == "/prefix/v2/signup/authentication-attempts"
    assert {request[0] for request in verifications} == {
        "/prefix/v2/signup/authentication-attempts/att_test/verify"
    }
    assert [request[2]["code"] for request in verifications] == codes
    verifiers = {request[2]["codeVerifier"] for request in verifications}
    assert len(verifiers) == 1
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifiers.pop().encode()).digest()
    )
    assert start[2]["codeChallenge"] == challenge.decode().rstrip("=")
    assert all("Idempotency-Key" not in request[1] for request in verifications)


@pytest.mark.parametrize("status", [403, 404, 429, 503, 307])
def test_retry_stops_on_terminal_verification_error(signup_server, status):
    signup_server["responses"][1:] = [
        (422, {}),
        (status, {"detail": "sensitive-server-detail"}),
    ]
    read_code = MagicMock(side_effect=["123456", "654321"])
    with pytest.raises(LoginError) as exc:
        asyncio.run(agent_login("owner@example.com", read_code=read_code))
    assert read_code.call_count == 2
    assert len(signup_server["requests"]) == 3
    assert "sensitive-server-detail" not in str(exc.value)
    if status == 404:
        assert "start login again" in str(exc.value)


def test_aborted_retry_does_not_send_another_request(signup_server):
    signup_server["responses"][1] = (422, {})
    read_code = MagicMock(side_effect=["123456", EOFError])
    with pytest.raises(EOFError):
        asyncio.run(agent_login("owner@example.com", read_code=read_code))
    assert read_code.call_count == 2
    assert len(signup_server["requests"]) == 2


@pytest.mark.parametrize("payload", [{}, {"attemptId": None}, {"attemptId": 1}, []])
def test_invalid_attempt_does_not_prompt(signup_server, payload):
    signup_server["responses"][0] = (201, payload)
    read_code = MagicMock()
    with pytest.raises(LoginError):
        asyncio.run(agent_login("owner@example.com", read_code=read_code))
    read_code.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        [],
        {},
        {"credential": "invalid"},
        {"credential": {"apiKey": " "}},
        {"credential": {"apiKey": 123}},
    ],
)
def test_invalid_key_response_fails_without_exposing_body(signup_server, payload):
    signup_server["responses"][1] = (200, payload)
    with pytest.raises(LoginError):
        asyncio.run(agent_login("owner@example.com", read_code=lambda: "123456"))


def test_aborted_code_prompt_does_not_verify(signup_server):
    read_code = MagicMock(side_effect=EOFError)
    with pytest.raises(EOFError):
        asyncio.run(agent_login("owner@example.com", read_code=read_code))
    assert len(signup_server["requests"]) == 1


@pytest.mark.parametrize("error", [asyncio.TimeoutError, aiohttp.ClientConnectionError])
def test_transport_failures_are_sanitized(error):
    session = MagicMock()
    session.post.return_value.__aenter__ = AsyncMock(side_effect=error("secret"))
    with pytest.raises(LoginError, match="check your connection") as exc:
        asyncio.run(_post_authentication_attempt(session, "/path", {}))
    assert "secret" not in str(exc.value)
    session.post.assert_called_once()
