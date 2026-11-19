"""request compatibility and unfollowed redirect handling."""

import importlib
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import click
import pytest

import runpod
from runpod.api.http import HTTPClient
from runpod.api.rest import AsyncRESTClient, RESTClient
from runpod.api.routes import Route, endpoint_base_url
from runpod.cli.api import api_errors
from runpod.error import AppError, AuthenticationError, QueryError


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/v2/pods/a{b}", "/v2/pods/a%7Bb%7D"),
        ('/search?q={"a":1}', "/search?q=%7B%22a%22:1%7D"),
    ],
)
def test_raw_paths_preserve_literal_braces(path, expected):
    assert Route("GET", path).url("https://example.test") == (
        "https://example.test" + expected
    )


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_sync_rest_rejects_redirect_without_exposing_body(status):
    response = MagicMock(status_code=status, text="synthetic-secret", headers={})
    session = MagicMock()
    session.request.return_value = response
    with RESTClient("synthetic-key", session=session) as client:
        with pytest.raises(QueryError, match="unexpected redirect") as caught:
            client.request(Route("DELETE", "/v2/pods/pod"))
    assert caught.value.status_code == status
    assert "synthetic-secret" not in str(caught.value)
    response.json.assert_not_called()
    response.close.assert_called_once()
    assert session.request.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_async_rest_rejects_redirect_without_exposing_body(status):
    response = MagicMock(status=status, headers={})
    response.text = AsyncMock(return_value="synthetic-secret")
    response.json = AsyncMock()
    session = MagicMock(closed=False)
    session.request.return_value.__aenter__.return_value = response
    async with AsyncRESTClient("synthetic-key", session=session) as client:
        with pytest.raises(QueryError, match="unexpected redirect") as caught:
            await client.request(Route("DELETE", "/v2/pods/pod"))
    assert caught.value.status_code == status
    assert "synthetic-secret" not in str(caught.value)
    response.json.assert_not_awaited()
    session.request.return_value.__aexit__.assert_awaited_once()
    assert session.request.call_args.kwargs["allow_redirects"] is False


async def test_borrowed_session_retry_policy_is_unchanged():
    async with aiohttp.ClientSession() as session:
        retry = session._retry_connection
        response = MagicMock(status=200)
        session.request = MagicMock()
        session.request.return_value.__aenter__.return_value = response
        async with HTTPClient(session=session) as client:
            for method in ("GET", "POST", "PUT", "DELETE"):
                async with client.stream(Route(method, "/work")):
                    assert session._retry_connection is retry
        assert session._retry_connection is retry
        assert not session.closed


async def test_closed_borrowed_session_fails_in_context_and_stream():
    session = aiohttp.ClientSession()
    await session.close()
    client = HTTPClient(session=session)
    with pytest.raises(RuntimeError, match="supplied HTTP session is closed"):
        async with client:
            pass
    with pytest.raises(RuntimeError, match="Session is closed"):
        async with client.stream(Route("GET", "/work")):
            pass


def test_programmatic_endpoint_base_takes_precedence(monkeypatch):
    monkeypatch.setenv("RUNPOD_ENDPOINT_BASE_URL", "https://environment.test/v2")
    monkeypatch.setattr(runpod, "endpoint_url_base", "https://explicit.test/v2")
    assert endpoint_base_url() == "https://explicit.test/v2"


@pytest.mark.parametrize(
    "base", ["relative", "file:///tmp", "https://user:secret@host"]
)
def test_invalid_api_base_has_clean_cli_error(monkeypatch, base):
    monkeypatch.setenv("RUNPOD_API_BASE_URL", base)
    with pytest.raises(click.ClickException, match="base URL must be an HTTP origin"):
        with api_errors():
            Route("GET", "/v2/pods").url()


@pytest.mark.parametrize(
    "module",
    [
        "api",
        "catalog",
        "compute",
        "console",
        "endpoint",
        "endpoint_config",
        "logs",
        "main",
        "options",
        "output",
        "pod",
        "update",
        "volume",
    ],
)
def test_cli_import_aliases_share_module_identity(module):
    assert importlib.import_module(f"runpod.rp_cli.{module}") is (
        importlib.import_module(f"runpod.cli.{module}")
    )


def test_login_import_and_exception_compatibility():
    login = importlib.import_module("runpod.auth.login")
    assert importlib.import_module("runpod.apps.auth") is login
    assert isinstance(login.LoginError("denied"), AuthenticationError)
    assert isinstance(login.LoginError("denied"), AppError)
