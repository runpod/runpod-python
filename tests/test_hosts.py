"""Service host resolution: env overrides read at call time, public defaults otherwise."""

import pytest

import runpod
from runpod import _hosts


@pytest.fixture(autouse=True)
def _clear_host_env(monkeypatch):
    for name in ("RUNPOD_CONSOLE_URL", "RUNPOD_PROXY_DOMAIN"):
        monkeypatch.delenv(name, raising=False)


class TestConsoleUrl:
    def test_defaults_to_public_console(self):
        assert _hosts.console_url() == "https://console.runpod.io"

    def test_env_override_is_read_at_call_time(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_CONSOLE_URL", "https://console.example.test/")
        assert _hosts.console_url() == "https://console.example.test"

    def test_blank_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_CONSOLE_URL", "   ")
        assert _hosts.console_url() == "https://console.runpod.io"


class TestProxyUrl:
    def test_defaults_to_public_proxy_domain(self):
        assert _hosts.proxy_url("pod1", 8080) == "https://pod1-8080.proxy.runpod.net"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_PROXY_DOMAIN", "proxy.example.test")
        assert (
            _hosts.proxy_url("pod1", "8888") == "https://pod1-8888.proxy.example.test"
        )

    def test_blank_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_PROXY_DOMAIN", "")
        assert _hosts.proxy_url("pod1", 80) == "https://pod1-80.proxy.runpod.net"


class TestEndpointDomain:
    def test_host_of_endpoint_base(self, monkeypatch):
        monkeypatch.setattr(runpod, "endpoint_url_base", "https://api.example.test/v2")
        assert _hosts.endpoint_domain() == "api.example.test"

    def test_default_endpoint_base(self, monkeypatch):
        monkeypatch.setattr(runpod, "endpoint_url_base", "https://api.runpod.ai/v2")
        assert _hosts.endpoint_domain() == "api.runpod.ai"
