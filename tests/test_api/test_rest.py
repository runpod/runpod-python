"""Tests for the REST API transport."""

from unittest.mock import MagicMock, Mock, patch

import pytest

import runpod
import requests

from runpod.api.rest import _parse_event_stream, read_event_stream, run_rest_request
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


def _stream_response(chunks, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.headers = {}
    response.__enter__.return_value = response

    def iter_content(chunk_size=None):
        for chunk in chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    response.iter_content.side_effect = iter_content
    return response


def test_parse_event_stream_reassembles_split_frames():
    chunks = [
        b"id: 1\r\ndata: {\"line\": \"caf",
        "\u00e9\"}\r\n\r\n: keep-alive\n\n".encode()[:1],
        "\u00e9\"}\r\n\r\n: keep-alive\n\n".encode()[1:],
        b"event: log\ndata: a\ndata: b\n\n",
        b"id: 3\ndata: incomplete",
    ]

    assert list(_parse_event_stream(iter(chunks))) == [
        {"id": "1", "data": '{"line": "caf\u00e9"}'},
        {"event": "log", "data": "a\nb"},
    ]


def test_read_event_stream_requests_sse():
    response = _stream_response([b"id: 1\ndata: x\n\n"])
    with patch("runpod.api.rest.requests.get", return_value=response) as get:
        events = list(
            read_event_stream(
                "/v2/pods/pod/logs", api_key="key", params={"tail": 5}, max_wait=2
            )
        )

    assert events == [{"id": "1", "data": "x"}]
    get.assert_called_once_with(
        "https://api.runpod.io/v2/pods/pod/logs",
        headers={
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "Authorization": "Bearer key",
        },
        params={"tail": 5},
        stream=True,
        timeout=(30, 2),
    )


def test_read_event_stream_raises_for_error_response():
    response = _stream_response([], status_code=404)
    response.json.return_value = {"detail": "pod not found"}
    with (
        patch("runpod.api.rest.requests.get", return_value=response),
        pytest.raises(QueryError, match="pod not found") as raised,
    ):
        list(read_event_stream("/v2/pods/pod/logs", api_key="key"))

    assert raised.value.status_code == 404


def test_read_event_stream_stops_at_deadline():
    response = _stream_response([b"data: 1\n\n", b"data: 2\n\n", b"data: 3\n\n"])
    with (
        patch("runpod.api.rest.requests.get", return_value=response),
        patch("runpod.api.rest.time.monotonic", side_effect=[0, 1, 5]),
    ):
        events = list(read_event_stream("/v2/pods/pod/logs", api_key="key", max_wait=5))

    assert events == [{"data": "1"}, {"data": "2"}]


def test_read_event_stream_ends_on_idle_timeout():
    response = _stream_response(
        [b"data: 1\n\n", requests.exceptions.ConnectionError("read timed out")]
    )
    with patch("runpod.api.rest.requests.get", return_value=response):
        events = list(read_event_stream("/v2/pods/pod/logs", api_key="key"))

    assert events == [{"data": "1"}]


def test_read_event_stream_resumes_and_uses_idle_timeout_without_deadline():
    response = _stream_response([b"id: 2\ndata: x\n\n"])
    with patch("runpod.api.rest.requests.get", return_value=response) as get:
        events = list(
            read_event_stream(
                "/v2/pods/pod/logs", api_key="key", max_wait=None, last_event_id="1"
            )
        )

    assert events == [{"id": "2", "data": "x"}]
    assert get.call_args.kwargs["headers"]["Last-Event-ID"] == "1"
    assert get.call_args.kwargs["timeout"] == (30, 60)


@pytest.mark.parametrize(("header", "expected"), [("12", 12.0), ("soon", None)])
def test_request_exposes_retry_after_on_rate_limit(header, expected):
    response = _response(status_code=429, payload={"detail": "rate limited"})
    response.headers = {"Retry-After": header}
    with (
        patch("runpod.api.rest.requests.request", return_value=response),
        pytest.raises(QueryError, match="rate limited") as raised,
    ):
        run_rest_request("GET", "/v2/pods", api_key="key")

    assert raised.value.status_code == 429
    assert raised.value.retry_after == expected
