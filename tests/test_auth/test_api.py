"""account authentication transport."""

from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

import runpod
from runpod.auth.api import AuthApiClient
from runpod.error import AuthenticationError, QueryError


@pytest.fixture(autouse=True)
def stored_key(monkeypatch):
    monkeypatch.setattr(runpod, "api_key", "stored-key")


def _respond(data):
    return AsyncMock(return_value={"data": data})


class TestAuthRequests:
    async def test_create_auth_request_is_anonymous(self):
        client = AuthApiClient()
        transport = _respond(
            {"createFlashAuthRequest": {"id": "req1", "status": "PENDING"}}
        )
        with patch("runpod.auth.api.run_graphql_query_async", transport):
            result = await client.create_auth_request()
        assert result["id"] == "req1"
        assert transport.call_args[1]["anonymous"] is True

    async def test_verify_api_key_sends_the_pasted_key(self):
        client = AuthApiClient()
        transport = _respond({"myself": {"id": "user1"}})
        with patch("runpod.auth.api.run_graphql_query_async", transport):
            assert await client.verify_api_key("rpa_new") is True
        assert transport.call_args[1]["api_key"] == "rpa_new"
        assert transport.call_args[1].get("anonymous", False) is False

    @pytest.mark.parametrize(
        "outcome",
        [
            AuthenticationError("Unauthorized request"),
            QueryError("invalid api key", "query"),
            {"data": {"myself": None}},
            {"data": None},
        ],
    )
    async def test_verify_api_key_rejects(self, outcome):
        transport = AsyncMock()
        if isinstance(outcome, Exception):
            transport.side_effect = outcome
        else:
            transport.return_value = outcome
        with patch("runpod.auth.api.run_graphql_query_async", transport):
            assert await AuthApiClient().verify_api_key("rpa_bad") is False

    async def test_verify_api_key_raises_on_transport_error(self):
        transport = AsyncMock(side_effect=aiohttp.ClientConnectionError("down"))
        with patch("runpod.auth.api.run_graphql_query_async", transport):
            with pytest.raises(aiohttp.ClientConnectionError):
                await AuthApiClient().verify_api_key("rpa_new")

    async def test_status_check_is_anonymous_and_never_asks_for_the_key(self):
        client = AuthApiClient()
        transport = _respond(
            {"flashAuthRequestStatus": {"id": "req1", "status": "DENIED"}}
        )
        with patch("runpod.auth.api.run_graphql_query_async", transport):
            result = await client.get_auth_request_status("req1")
        assert result["status"] == "DENIED"
        assert transport.call_args[1]["anonymous"] is True
        assert "apiKey" not in transport.call_args[0][0]
