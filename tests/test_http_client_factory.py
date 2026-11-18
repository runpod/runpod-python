"""Exercise real aiohttp session options without sending requests."""

import aiohttp
import pytest
import pytest_asyncio

from runpod import http_client


@pytest_asyncio.fixture(autouse=True)
async def controlled_defaults(monkeypatch):
    """Avoid credential access and close every connector allocated by the factory."""
    connectors = []
    original = http_client.TCPConnector

    def connector(*args, **kwargs):
        created = original(*args, **kwargs)
        connectors.append(created)
        return created

    monkeypatch.setattr(http_client, "TCPConnector", connector)
    monkeypatch.setattr(http_client, "get_auth_header", lambda: {"X-Test": "default"})
    try:
        yield connectors
    finally:
        for created in connectors:
            await created.close()


@pytest.mark.asyncio
async def test_default_session_options():
    async with http_client.AsyncClientSession() as session:
        assert session.connector.limit == 0
        assert session.timeout.total == 600
        assert session.timeout.ceil_threshold == 400
        assert session.headers["X-Test"] == "default"
    assert session.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["timeout", "headers"])
async def test_explicit_session_options(option):
    value = (
        aiohttp.ClientTimeout(total=5) if option == "timeout" else {"X-Test": "caller"}
    )
    async with http_client.AsyncClientSession(**{option: value}) as session:
        if option == "timeout":
            assert session.timeout is value
            assert session.headers["X-Test"] == "default"
        else:
            assert session.headers["X-Test"] == "caller"
            assert session.timeout.total == 600
    assert session.closed


@pytest.mark.asyncio
async def test_caller_owned_connector(controlled_defaults):
    connector = aiohttp.TCPConnector(limit=2)
    try:
        async with http_client.AsyncClientSession(
            connector=connector, connector_owner=False
        ) as session:
            assert session.connector is connector
            assert session.connector.limit == 2
            assert controlled_defaults == []
        assert session.closed
        assert not connector.closed
    finally:
        await connector.close()


@pytest.mark.asyncio
async def test_explicit_none_uses_aiohttp_connector_default(controlled_defaults):
    async with http_client.AsyncClientSession(connector=None) as session:
        assert session.connector.limit == 100
        assert controlled_defaults == []
    assert session.closed
