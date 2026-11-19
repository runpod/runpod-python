"""Endpoint route, response lifetime, and session ownership tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import aiohttp
import pytest
import requests

import runpod
from runpod.endpoint.asyncio import asyncio_runner
from runpod.endpoint.helpers import UNAUTHORIZED_MSG
from runpod.endpoint.runner import Endpoint, Job, RunPodClient


@pytest.mark.parametrize(
    ("operation", "method", "path", "timeout"),
    [
        ("run", "POST", "run", 10),
        ("run_sync", "POST", "runsync", 86400),
        ("health", "GET", "health", 3),
        ("purge_queue", "POST", "purge-queue", 3),
        ("status", "GET", "status/job%2F%3F%23%25", 10),
        ("stream", "GET", "stream/job%2F%3F%23%25", 10),
        ("cancel", "POST", "cancel/job%2F%3F%23%25", 3),
    ],
)
def test_sync_routes_and_response_release(operation, method, path, timeout):
    response = Mock(status_code=200)
    response.json.return_value = {"id": "job/?#%", "status": "COMPLETED"}
    with (
        patch.object(runpod, "endpoint_url_base", "https://example.com/v2/"),
        patch("requests.Session.request", return_value=response) as request,
    ):
        with Endpoint("endpoint/?#%", api_key="endpoint-key") as endpoint:
            if operation in {"run", "run_sync"}:
                getattr(endpoint, operation)({"message": "hello"})
            elif operation in {"health", "purge_queue"}:
                getattr(endpoint, operation)()
            else:
                job = Job(endpoint.endpoint_id, "job/?#%", endpoint.rp_client)
                if operation == "stream":
                    job._fetch_job("stream")
                else:
                    getattr(job, operation)()

    assert request.call_args.args == (
        method,
        f"https://example.com/v2/endpoint%2F%3F%23%25/{path}",
    )
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer endpoint-key"
    assert request.call_args.kwargs["timeout"] == timeout
    assert request.call_args.kwargs["allow_redirects"] is False
    response.close.assert_called_once_with()


def test_sync_endpoint_reuses_and_closes_its_pool():
    session = Mock()
    session.request.return_value.status_code = 200
    session.request.return_value.json.return_value = {
        "id": "job",
        "status": "COMPLETED",
    }
    with patch("runpod.endpoint.runner.requests.Session", return_value=session):
        with Endpoint("endpoint", api_key="key") as endpoint:
            job = endpoint.run({"message": "hello"})
            assert job.status() == "COMPLETED"
            assert job.rp_client is endpoint.rp_client
            assert endpoint.rp_client.rp_session is session
            assert session.request.call_count == 2
            assert session.request.return_value.close.call_count == 2
            session.close.assert_not_called()
        session.close.assert_called_once_with()


@pytest.mark.parametrize("failure", ["unauthorized", "http", "json"])
def test_sync_releases_response_and_pool_on_errors(failure):
    response = Mock(status_code=401 if failure == "unauthorized" else 500)
    if failure == "http":
        response.raise_for_status.side_effect = requests.HTTPError("server error")
        expected = requests.HTTPError
        message = "server error"
    elif failure == "json":
        response.status_code = 200
        response.json.side_effect = ValueError("invalid json")
        expected = ValueError
        message = "invalid json"
    else:
        expected = RuntimeError
        message = UNAUTHORIZED_MSG

    with (
        patch("requests.Session.request", return_value=response),
        patch("requests.Session.close") as close,
    ):
        with pytest.raises(expected) as error:
            with RunPodClient(api_key="key") as client:
                client.get("endpoint/status/job")
        assert str(error.value) == message
        close.assert_called_once_with()
    response.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_async_routes_reuse_the_borrowed_session():
    response = MagicMock()
    response.json = AsyncMock(
        return_value={"id": "job/?#%", "status": "COMPLETED", "output": "done"}
    )
    context = MagicMock()
    context.__aenter__.return_value = response
    with patch.object(runpod, "endpoint_url_base", "https://example.com/v2/"):
        timeout = aiohttp.ClientTimeout(total=900, connect=45, sock_read=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            with patch.object(session, "request", return_value=context) as request:
                endpoint = asyncio_runner.Endpoint(
                    "endpoint/?#%", session, api_key="key"
                )
                job = await endpoint.run({"message": "hello"})
                await endpoint.health()
                await endpoint.purge_queue()
                assert await job.status() == "COMPLETED"
                await job._fetch_job("stream")
                await job.cancel()
                assert job.session is session
                assert not session.closed

            assert [
                (call.args[0], str(call.args[1])) for call in request.call_args_list
            ] == [
                ("POST", "https://example.com/v2/endpoint%2F%3F%23%25/run"),
                ("GET", "https://example.com/v2/endpoint%2F%3F%23%25/health"),
                ("POST", "https://example.com/v2/endpoint%2F%3F%23%25/purge-queue"),
                (
                    "GET",
                    "https://example.com/v2/endpoint%2F%3F%23%25/status/job%2F%3F%23%25",
                ),
                (
                    "GET",
                    "https://example.com/v2/endpoint%2F%3F%23%25/stream/job%2F%3F%23%25",
                ),
                (
                    "POST",
                    "https://example.com/v2/endpoint%2F%3F%23%25/cancel/job%2F%3F%23%25",
                ),
            ]
            for call in request.call_args_list:
                assert call.kwargs["headers"]["Authorization"] == "Bearer key"
                assert call.kwargs["timeout"] is timeout
                assert call.kwargs["allow_redirects"] is False
            assert request.call_args_list[0].kwargs["json"] == {
                "input": {"message": "hello"}
            }
            assert request.call_args.kwargs["headers"]["X-Request-ID"] == "job/?#%"
            assert context.__aexit__.await_count == 6
            response.raise_for_status.assert_not_called()
        assert session.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["run", "health", "purge_queue", "status", "cancel"]
)
async def test_async_json_errors_release_response_without_closing_session(operation):
    response = MagicMock()
    response.json = AsyncMock(side_effect=ValueError("invalid json"))
    context = MagicMock()
    context.__aenter__.return_value = response
    async with aiohttp.ClientSession() as session:
        with patch.object(session, "request", return_value=context):
            endpoint = asyncio_runner.Endpoint("endpoint", session, api_key="key")
            job = asyncio_runner.Job("endpoint", "job", session, endpoint.headers)
            with pytest.raises(ValueError, match="invalid json"):
                if operation == "run":
                    await endpoint.run({})
                elif operation in {"status", "cancel"}:
                    await getattr(job, operation)()
                else:
                    await getattr(endpoint, operation)()
            context.__aexit__.assert_awaited_once()
            assert not session.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 500])
async def test_async_preserves_error_response_json(status):
    response = MagicMock(status=status)
    response.json = AsyncMock(return_value={"error": "request failed"})
    context = MagicMock()
    context.__aenter__.return_value = response
    async with aiohttp.ClientSession() as session:
        with patch.object(session, "request", return_value=context):
            endpoint = asyncio_runner.Endpoint("endpoint", session, api_key="key")
            assert await endpoint.health() == {"error": "request failed"}
        response.raise_for_status.assert_not_called()
        context.__aexit__.assert_awaited_once()
        assert not session.closed


@pytest.mark.asyncio
async def test_async_cancellation_releases_response_without_closing_session():
    decoding = asyncio.Event()

    async def read_json():
        decoding.set()
        await asyncio.Future()

    response = MagicMock()
    response.json = AsyncMock(side_effect=read_json)
    context = MagicMock()
    context.__aenter__.return_value = response
    async with aiohttp.ClientSession() as session:
        with patch.object(session, "request", return_value=context):
            endpoint = asyncio_runner.Endpoint("endpoint", session, api_key="key")
            task = asyncio.create_task(endpoint.run({}))
            await decoding.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        context.__aexit__.assert_awaited_once()
        assert not session.closed
