"""
Test fixtures for serverless core components.

Provides reusable fixtures for testing job scheduler, state management,
heartbeat, and other core functionality.
"""

import asyncio
from typing import Dict, Any, List
from unittest.mock import AsyncMock, Mock
import pytest
import aiohttp


@pytest.fixture
def sample_job() -> Dict[str, Any]:
    """Provide sample job data for testing."""
    return {
        "id": "test-job-123",
        "input": {"prompt": "test prompt", "value": 42},
        "mock_delay": 2
    }


@pytest.fixture
def sample_jobs() -> List[Dict[str, Any]]:
    """Provide multiple sample jobs."""
    return [
        {"id": f"job-{i}", "input": {"value": i * 10}, "mock_delay": 1}
        for i in range(5)
    ]


@pytest.fixture
async def mock_session() -> AsyncMock:
    """Provide mock aiohttp ClientSession."""
    session = AsyncMock(spec=aiohttp.ClientSession)

    # Setup default response mock
    response_mock = AsyncMock()
    response_mock.status = 200
    response_mock.json = AsyncMock(return_value={})
    response_mock.raise_for_status = AsyncMock(return_value=None)

    # Setup context manager for get/post
    session.get.return_value.__aenter__.return_value = response_mock
    session.get.return_value.__aexit__.return_value = None

    session.post.return_value.__aenter__.return_value = response_mock
    session.post.return_value.__aexit__.return_value = None

    return session


@pytest.fixture
def mock_handler() -> Mock:
    """Provide mock handler function."""
    handler = Mock(return_value={"output": "success"})
    handler.__name__ = "mock_handler"
    return handler


@pytest.fixture
async def mock_async_handler() -> AsyncMock:
    """Provide mock async handler function."""
    async def handler(job: Dict[str, Any]) -> Dict[str, Any]:
        await asyncio.sleep(0.01)
        return {"output": f"processed-{job['id']}"}

    return handler


@pytest.fixture
def blocking_handler() -> callable:
    """Provide blocking (CPU-intensive) handler."""
    import time

    def handler(job: Dict[str, Any]) -> Dict[str, Any]:
        time.sleep(0.5)  # Simulate CPU work
        return {"output": f"blocked-{job['id']}"}

    return handler


@pytest.fixture
async def mock_session_with_long_poll() -> AsyncMock:
    """
    Provide mock session that simulates long-polling behavior.

    The backend holds connections open, so the mock needs to block
    (sleep) before returning 204 to simulate realistic behavior.
    """
    session = AsyncMock(spec=aiohttp.ClientSession)

    # Response that simulates long-poll timeout (sleeps then returns 204)
    async def long_poll_get(*args, **kwargs):
        response_mock = AsyncMock()
        response_mock.status = 204
        response_mock.json = AsyncMock(return_value={})
        response_mock.raise_for_status = AsyncMock(return_value=None)

        # Simulate backend holding connection open
        await asyncio.sleep(0.05)

        return response_mock

    # Setup context manager behavior
    session.get.return_value.__aenter__.side_effect = long_poll_get
    session.get.return_value.__aexit__.return_value = None

    # Setup post mock (doesn't need long-poll)
    response_mock = AsyncMock()
    response_mock.status = 200
    response_mock.raise_for_status = AsyncMock(return_value=None)
    session.post.return_value.__aenter__.return_value = response_mock
    session.post.return_value.__aexit__.return_value = None

    return session
