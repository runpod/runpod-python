"""Tests for the REST API transport."""

from unittest.mock import Mock, patch

import pytest

import runpod
from runpod.api.rest import run_rest_request
from runpod.error import AuthenticationError, QueryError
from runpod.user_agent import USER_AGENT


def _response(status_code=200, payload=None, content=b"{}", text=""):
    response = Mock()
    response.status_code = status_code
    response.content = content
    response.text = text
    response.json.return_value = payload if payload is not None else {}
    return response


def test_request_uses_explicit_api_key():
    response = _response(payload={"pods": []})
    with patch("runpod.api.rest.requests.request", return_value=response) as request:
        result = run_rest_request(
            "POST",
            "/v2/pods",
            api_key="key",
            params={"include": "all"},
            json={"name": "pod"},
        )

    assert result == {"pods": []}
    request.assert_called_once_with(
        "POST",
        "https://api.runpod.io/v2/pods",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Authorization": "Bearer key",
        },
        params={"include": "all"},
        json={"name": "pod"},
        timeout=30,
    )


def test_request_uses_global_api_key_and_custom_base_url():
    response = _response(payload={"gpus": []})
    with (
        patch.object(runpod, "api_key", "global-key"),
        patch.dict("os.environ", {"RUNPOD_API_BASE_URL": "https://example.test/"}),
        patch("runpod.api.rest.requests.request", return_value=response) as request,
    ):
        run_rest_request("GET", "/v2/catalog/gpus")

    assert request.call_args.args[1] == "https://example.test/v2/catalog/gpus"
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer global-key"


def test_request_requires_api_key():
    with (
        patch.object(runpod, "api_key", None),
        patch("runpod.api.rest.requests.request") as request,
        pytest.raises(AuthenticationError, match="No API key provided"),
    ):
        run_rest_request("GET", "/v2/pods")

    request.assert_not_called()


def test_request_returns_none_for_no_content():
    response = _response(status_code=204, content=b"")
    with patch("runpod.api.rest.requests.request", return_value=response):
        assert run_rest_request("DELETE", "/v2/pods/pod", api_key="key") is None


def test_request_raises_authentication_error_for_unauthorized_response():
    response = _response(status_code=401, payload={"detail": "invalid key"})
    with (
        patch("runpod.api.rest.requests.request", return_value=response),
        pytest.raises(AuthenticationError, match="Unauthorized request"),
    ):
        run_rest_request("GET", "/v2/pods", api_key="key")


def test_request_raises_query_error_from_problem_response():
    response = _response(
        status_code=422,
        payload={
            "title": "Unprocessable Entity",
            "detail": "request validation failed",
            "errors": ["$.name is required"],
        },
    )
    with (
        patch("runpod.api.rest.requests.request", return_value=response),
        pytest.raises(QueryError, match="request validation failed") as raised,
    ):
        run_rest_request("POST", "/v2/pods", api_key="key", json={})

    assert raised.value.query == "POST /v2/pods"
    assert raised.value.status_code == 422
    assert raised.value.errors == ["$.name is required"]


def test_request_uses_text_for_non_json_error():
    response = _response(status_code=500, content=b"failure", text="upstream failure")
    response.json.side_effect = ValueError
    with (
        patch("runpod.api.rest.requests.request", return_value=response),
        pytest.raises(QueryError, match="upstream failure") as raised,
    ):
        run_rest_request("GET", "/v2/pods", api_key="key")

    assert raised.value.status_code == 500
