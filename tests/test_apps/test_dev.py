"""tests for dev session provisioning."""

from unittest.mock import AsyncMock

import pytest

import runpod
from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.dev import DevSession, _endpoint_input, dev_endpoint_name
from runpod.apps.targets import LiveTarget


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


def _mock_api():
    api = AsyncMock()
    api.list_my_endpoints.return_value = []
    api.save_endpoint.return_value = {"id": "ep-new"}
    api.delete_endpoint.return_value = True
    return api


class TestEndpointInput:
    def test_cpu_queue_payload(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2", workers=(0, 1))
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        assert payload["name"] == "dev-a-q"
        assert payload["instanceIds"] == ["cpu3c-1-2"]
        assert payload["locations"] == "EU-RO-1"
        assert payload["scalerType"] == "QUEUE_DELAY"
        template = payload["template"]
        assert template["dockerArgs"] == ""
        assert template["env"] == []
        assert "gpuIds" not in payload

    def test_gpu_queue_payload(self):
        app = App("a")

        @app.queue(name="q", gpu=runpod.GpuGroup.ADA_24)
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        assert payload["gpuIds"] == "ADA_24"
        assert payload["gpuCount"] == 1
        assert "instanceIds" not in payload
        assert "locations" not in payload

    def test_api_payload_is_lb(self):
        app = App("a")

        @app.api(name="api", cpu="cpu3c-1-2")
        class Api:
            @runpod.post("/x")
            def x(self, body: dict):
                return body

        payload = _endpoint_input(app, Api.spec)
        assert payload["type"] == "LB"
        assert payload["scalerType"] == "REQUEST_COUNT"

    def test_datacenter_pins_locations(self):
        app = App("a")

        @app.queue(name="q", gpu=runpod.GpuGroup.ADA_24, datacenter="US-KS-2")
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        assert payload["locations"] == "US-KS-2"

    def test_env_forwarded_to_template(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2", env={"K": "v"})
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        assert payload["template"]["env"] == [{"key": "K", "value": "v"}]


class TestDevSession:
    async def test_start_provisions_and_registers_targets(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        api = _mock_api()
        session = DevSession([app], api=api)
        await session.start()

        api.save_endpoint.assert_awaited_once()
        target = app._dev_targets["q"]
        assert isinstance(target, LiveTarget)
        assert target.endpoint_id == "ep-new"

    async def test_start_adopts_existing_endpoint(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        api = _mock_api()
        api.list_my_endpoints.return_value = [
            {"id": "ep-old", "name": dev_endpoint_name("a", "q")}
        ]
        session = DevSession([app], api=api)
        await session.start()

        api.save_endpoint.assert_not_awaited()
        assert app._dev_targets["q"].endpoint_id == "ep-old"

    async def test_stop_deletes_all_session_endpoints(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        api = _mock_api()
        session = DevSession([app], api=api)
        await session.start()
        await session.stop()

        api.delete_endpoint.assert_awaited_once_with("ep-new")
        assert app._dev_targets == {}

    async def test_tasks_not_provisioned(self):
        app = App("a")

        @app.task(name="t", cpu="cpu3c-1-2")
        def t():
            pass

        api = _mock_api()
        session = DevSession([app], api=api)
        await session.start()

        api.save_endpoint.assert_not_awaited()
