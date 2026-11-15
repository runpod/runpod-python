"""browser login flow."""

import asyncio
from unittest.mock import AsyncMock

import aiohttp
import pytest

from runpod.apps.auth import LoginError, auth_url, browser_login


def _api(valid_keys=("rpa_new",), status="PENDING"):
    api = AsyncMock()
    api.get_auth_request_status.return_value = {"id": "req-1", "status": status}
    api.create_auth_request.return_value = {
        "id": "req-1",
        "status": "PENDING",
        "expiresAt": None,
    }
    api.verify_api_key.side_effect = lambda key: key in valid_keys
    return api


def _prompt(*answers):
    answers = iter(answers)
    return lambda: next(answers)


class TestBrowserLogin:
    def test_returns_pasted_key_after_showing_url(self):
        api = _api()
        urls = []
        key = asyncio.run(
            browser_login(
                api=api, prompt_key=_prompt("  rpa_new\n"), on_url=urls.append
            )
        )
        assert key == "rpa_new"
        assert urls == [auth_url("req-1")]
        api.verify_api_key.assert_awaited_once_with("rpa_new")

    def test_never_polls_for_the_key(self):
        api = _api()
        asyncio.run(browser_login(api=api, prompt_key=_prompt("rpa_new")))
        assert [c[0] for c in api.method_calls] == [
            "create_auth_request",
            "verify_api_key",
        ]

    def test_retries_after_empty_and_rejected_keys(self):
        api = _api()
        reasons = []
        key = asyncio.run(
            browser_login(
                api=api,
                prompt_key=_prompt("   ", "rpa_bad", "rpa_new"),
                on_rejected=reasons.append,
            )
        )
        assert key == "rpa_new"
        assert reasons == ["no api key entered", "api key was rejected"]
        # empty input is rejected without a network call
        assert api.verify_api_key.await_count == 2

    def test_gives_up_after_max_attempts(self):
        api = _api()
        reasons = []
        with pytest.raises(LoginError, match="rejected; run login again"):
            asyncio.run(
                browser_login(
                    api=api,
                    prompt_key=_prompt("a", "b", "c", "rpa_new"),
                    on_rejected=reasons.append,
                )
            )
        assert len(reasons) == 2
        assert api.verify_api_key.await_count == 3

    def test_none_from_prompt_counts_as_empty(self):
        api = _api()
        with pytest.raises(LoginError, match="no api key entered"):
            asyncio.run(browser_login(api=api, prompt_key=lambda: None, max_attempts=1))
        api.verify_api_key.assert_not_awaited()

    @pytest.mark.parametrize(
        "status, message",
        [("DENIED", "denied in the browser"), ("EXPIRED", "request expired")],
    )
    def test_stops_prompting_once_the_request_is_closed(self, status, message):
        api = _api(status=status)
        reasons = []
        with pytest.raises(LoginError, match=message):
            asyncio.run(
                browser_login(
                    api=api,
                    prompt_key=_prompt("", "rpa_new"),
                    on_rejected=reasons.append,
                )
            )
        assert reasons == []
        api.get_auth_request_status.assert_awaited_once_with("req-1")
        api.verify_api_key.assert_not_awaited()

    def test_checks_status_after_a_rejected_key(self):
        api = _api(status="DENIED")
        with pytest.raises(LoginError, match="denied"):
            asyncio.run(browser_login(api=api, prompt_key=_prompt("rpa_bad")))
        api.verify_api_key.assert_awaited_once_with("rpa_bad")

    def test_keeps_prompting_when_the_status_check_fails(self):
        api = _api()
        api.get_auth_request_status.side_effect = aiohttp.ClientConnectionError("down")
        key = asyncio.run(browser_login(api=api, prompt_key=_prompt("", "rpa_new")))
        assert key == "rpa_new"

    def test_missing_request_id_raises_before_prompting(self):
        api = AsyncMock()
        api.create_auth_request.return_value = {}
        prompted = []
        with pytest.raises(LoginError, match="initialize"):
            asyncio.run(browser_login(api=api, prompt_key=lambda: prompted.append(1)))
        assert prompted == []


def test_auth_url_honors_console_override_at_call_time(monkeypatch):
    monkeypatch.setenv("RUNPOD_CONSOLE_URL", "https://console.example.test")
    assert auth_url("req-1") == "https://console.example.test/flash/login?request=req-1"
