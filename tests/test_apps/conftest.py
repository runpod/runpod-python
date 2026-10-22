"""shared fixtures for apps tests."""

from unittest.mock import AsyncMock

import pytest

from runpod.apps import gpu as _gpu


@pytest.fixture(autouse=True)
def offline_gpu_catalog(monkeypatch):
    """payload builders read the live gpu catalog; keep tests offline and
    deterministic on the static pool table unless a test overrides it."""
    monkeypatch.setattr(_gpu, "live_pool_members", lambda: None)
    monkeypatch.setattr(_gpu, "live_pool_members_async", AsyncMock(return_value=None))
