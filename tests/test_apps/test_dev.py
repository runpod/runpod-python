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
        assert "EU-RO-1" in payload["locations"]
        assert "US-KS-2" in payload["locations"]
        assert payload["scalerType"] == "QUEUE_DELAY"
        assert payload["flashBootType"] == "FLASHBOOT"

    def test_cpu5_pins_to_stocked_datacenters(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu5c-2-4")
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        assert payload["locations"] == "EU-RO-1"

    def test_cpu_locations_are_storage_supported(self):
        from runpod.apps.datacenter import DataCenter

        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        valid = {dc.value for dc in DataCenter}
        for loc in payload["locations"].split(","):
            assert loc in valid
        template = payload["template"]
        assert template["dockerArgs"] == ""
        assert template["env"] == [
            {"key": "RUNPOD_DEV_GENERATION", "value": "1"}
        ]
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
        assert {"key": "K", "value": "v"} in payload["template"]["env"]

    def test_generation_stamped_in_template_env(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        payload = _endpoint_input(app, q.spec, generation=7)
        assert {"key": "RUNPOD_DEV_GENERATION", "value": "7"} in payload[
            "template"
        ]["env"]


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

    async def test_start_adopts_and_reconciles_existing_endpoint(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        api = _mock_api()
        api.list_my_endpoints.return_value = [
            {"id": "ep-old", "name": dev_endpoint_name("a", "q")}
        ]
        api.save_endpoint.return_value = {"id": "ep-old"}
        session = DevSession([app], api=api)
        await session.start()

        # adopted endpoints are reconciled via saveEndpoint with the id set
        payload = api.save_endpoint.await_args.args[0]
        assert payload["id"] == "ep-old"
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


class TestDevRefresh:
    async def test_refresh_bumps_generation_and_updates(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        api = _mock_api()
        api.save_endpoint.return_value = {"id": "ep-1"}
        session = DevSession([app], api=api)
        await session.start()

        await session.refresh([app])

        payload = api.save_endpoint.await_args.args[0]
        assert payload["id"] == "ep-1"
        assert {"key": "RUNPOD_DEV_GENERATION", "value": "2"} in payload[
            "template"
        ]["env"]
        assert session.generation == 2

    async def test_refresh_provisions_added_resource(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        api = _mock_api()
        api.save_endpoint.return_value = {"id": "ep-1"}
        session = DevSession([app], api=api)
        await session.start()

        _clear_registry()
        app2 = App("a")

        @app2.queue(name="q", cpu="cpu3c-1-2")
        def q2():
            pass

        @app2.queue(name="extra", cpu="cpu3c-1-2")
        def extra():
            pass

        api.save_endpoint.return_value = {"id": "ep-2"}
        await session.refresh([app2])

        names = {
            c.args[0]["name"] for c in api.save_endpoint.await_args_list
        }
        assert dev_endpoint_name("a", "extra") in names
        assert "extra" in app2._dev_targets

    async def test_refresh_deletes_removed_resource(self):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        @app.queue(name="gone", cpu="cpu3c-1-2")
        def gone():
            pass

        api = _mock_api()
        api.save_endpoint.side_effect = [{"id": "ep-q"}, {"id": "ep-gone"}]
        session = DevSession([app], api=api)
        await session.start()

        _clear_registry()
        app2 = App("a")

        @app2.queue(name="q", cpu="cpu3c-1-2")
        def q2():
            pass

        api.save_endpoint.side_effect = None
        api.save_endpoint.return_value = {"id": "ep-q"}
        await session.refresh([app2])

        api.delete_endpoint.assert_awaited_once_with("ep-gone")
