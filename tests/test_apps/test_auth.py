"""browser login flow."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from runpod.apps.auth import LoginError, auth_url, browser_login


def _api(statuses):
    api = AsyncMock()
    api.create_auth_request.return_value = {
        "id": "req-1",
        "status": "PENDING",
        "expiresAt": None,
    }
    api.get_auth_request_status.side_effect = statuses
    return api


class TestBrowserLogin:
    def test_returns_key_on_approval(self, monkeypatch):
        monkeypatch.setattr("runpod.apps.auth.POLL_INTERVAL_SECONDS", 0)
        api = _api(
            [
                {"status": "PENDING"},
                {"status": "APPROVED", "apiKey": "rpa_new"},
            ]
        )
        urls = []
        key = asyncio.run(
            browser_login(api=api, on_url=urls.append)
        )
        assert key == "rpa_new"
        assert urls == [auth_url("req-1")]

    def test_denied_raises(self, monkeypatch):
        monkeypatch.setattr("runpod.apps.auth.POLL_INTERVAL_SECONDS", 0)
        api = _api([{"status": "DENIED"}])
        with pytest.raises(LoginError, match="denied"):
            asyncio.run(browser_login(api=api))

    def test_timeout_raises(self, monkeypatch):
        monkeypatch.setattr("runpod.apps.auth.POLL_INTERVAL_SECONDS", 0)
        api = _api([{"status": "PENDING"}] * 50)
        with pytest.raises(LoginError, match="timed out"):
            asyncio.run(browser_login(api=api, timeout_seconds=0))

    def test_missing_request_id_raises(self):
        api = AsyncMock()
        api.create_auth_request.return_value = {}
        with pytest.raises(LoginError, match="initialize"):
            asyncio.run(browser_login(api=api))

    def test_consumed_without_key_raises(self, monkeypatch):
        monkeypatch.setattr("runpod.apps.auth.POLL_INTERVAL_SECONDS", 0)
        api = _api([{"status": "CONSUMED"}])
        with pytest.raises(LoginError, match="already used"):
            asyncio.run(browser_login(api=api))
