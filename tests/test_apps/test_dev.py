"""tests for dev session provisioning."""

from unittest.mock import AsyncMock

import pytest

import runpod
from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.dev import DevSession, _endpoint_input, dev_endpoint_name
from runpod.apps.errors import AppError
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
        assert payload["name"].startswith("dev-a-q-")
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
        env = {e["key"]: e["value"] for e in template["env"]}
        assert env["RUNPOD_DEV_GENERATION"] == "1"
        # nested .remote() support: dev-session marker always present
        assert env["RUNPOD_DEV_APP"] == app.name
        assert env["RUNPOD_DEV_RESOURCE"] == q.spec.name
        assert "FLASH_RESOURCE_NAME" not in env
        assert "RUNPOD_RESOURCE_NAME" not in env

    def test_api_key_forwarded_when_configured(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_API_KEY", "rpa_test123")
        app = App("keyed")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        env = {e["key"]: e["value"] for e in payload["template"]["env"]}
        assert env["RUNPOD_API_KEY"] == "rpa_test123"
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
        assert payload["template"]["ports"] == "80/http"
        env = {entry["key"]: entry["value"] for entry in payload["template"]["env"]}
        assert env["PORT"] == "80"
        assert env["PORT_HEALTH"] == "80"

    def test_custom_image_starts_external_runtime(self, monkeypatch):
        monkeypatch.setenv(
            "RUNPOD_RUNTIME_PACKAGE_SPEC", "runpod-sdk-runtime==1.2.3"
        )
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2", image="my/image:1")
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        template = payload["template"]
        env = {entry["key"]: entry["value"] for entry in template["env"]}
        assert template["imageName"] == "my/image:1"
        assert "runpod_sdk_runtime.bootstrap" in template["dockerArgs"]
        assert env["RUNPOD_RUNTIME_KIND"] == "queue"
        assert env["RUNPOD_RUNTIME_PACKAGE_SPEC"] == (
            "runpod-sdk-runtime==1.2.3"
        )

    def test_custom_api_uses_shared_bootstrap(self):
        app = App("a")

        @app.api(name="api", cpu="cpu3c-1-2", image="my/api:1")
        class Api:
            @runpod.post("/x")
            def x(self, body: dict):
                return body

        payload = _endpoint_input(app, Api.spec)
        assert payload["type"] == "LB"
        assert "runpod_sdk_runtime.bootstrap" in payload["template"]["dockerArgs"]

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

    async def test_worker_self_call_stays_local(self, monkeypatch):
        app = App("self-call")

        @app.queue(name="q-basic", cpu="cpu3c-1-2")
        def q(value):
            return value + 1

        payload = _endpoint_input(app, q.spec)
        for entry in payload["template"]["env"]:
            monkeypatch.setenv(entry["key"], entry["value"])
        monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep-self")
        monkeypatch.setattr(
            app, "_resolve", AsyncMock(side_effect=AssertionError("remote self-call"))
        )

        assert await q.remote.aio(4) == 5


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

    async def test_stop_retains_failed_ids_and_retries_only_failures(self):
        app = App("cleanup")
        for name in ("first-failed", "deleted", "second-failed"):
            app.queue(name=name, cpu="cpu3c-1-2")(lambda: None)
        api = _mock_api()
        api.save_endpoint.side_effect = [
            {"id": "ep-first"}, {"id": "ep-deleted"}, {"id": "ep-second"}
        ]
        session = DevSession([app], api=api)
        await session.start()
        api.delete_endpoint.side_effect = [
            RuntimeError("unauthorized"), True, RuntimeError("unavailable")
        ]

        with pytest.raises(AppError) as error:
            await session.stop()
        assert "ep-first" in str(error.value)
        assert "ep-second" in str(error.value)
        assert set(session._endpoint_ids) == {"ep-first", "ep-second"}

        api.delete_endpoint.reset_mock(side_effect=True)
        await session.stop()
        assert {call.args[0] for call in api.delete_endpoint.await_args_list} == {
            "ep-first", "ep-second"
        }
        assert session._endpoint_ids == []

    async def test_context_entry_failure_cleans_already_created_endpoints(self):
        app = App("partial")
        for name in ("created", "fails"):
            app.queue(name=name, cpu="cpu3c-1-2")(lambda: None)
        api = _mock_api()
        api.save_endpoint.side_effect = [
            {"id": "ep-created"}, RuntimeError("capacity")
        ]

        with pytest.raises(RuntimeError, match="capacity"):
            async with DevSession([app], api=api):
                pytest.fail("startup must fail")
        api.delete_endpoint.assert_awaited_once_with("ep-created")

    async def test_failed_adoption_remains_tracked_for_cleanup(self):
        app = App("adopt")
        app.queue(name="q-basic", cpu="cpu3c-1-2")(lambda: None)
        api = _mock_api()
        api.list_my_endpoints.return_value = [
            {"id": "ep-adopted", "name": dev_endpoint_name("adopt", "q-basic")}
        ]
        api.save_endpoint.side_effect = RuntimeError("update failed")
        session = DevSession([app], api=api)

        with pytest.raises(RuntimeError, match="update failed"):
            async with session:
                pytest.fail("startup must fail")
        api.delete_endpoint.assert_awaited_once_with("ep-adopted")


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

    async def test_failed_removals_remain_tracked_for_stop(self):
        app = App("remove")
        for name in ("q-basic", "q-other"):
            app.queue(name=name, cpu="cpu3c-1-2")(lambda: None)
        api = _mock_api()
        api.save_endpoint.side_effect = [{"id": "ep-basic"}, {"id": "ep-other"}]
        session = DevSession([app], api=api)
        await session.start()
        api.delete_endpoint.side_effect = [RuntimeError("failed"), True]

        with pytest.raises(AppError, match="ep-basic"):
            await session.refresh([])
        assert session._endpoint_ids == ["ep-basic"]

        api.delete_endpoint.reset_mock(side_effect=True)
        await session.stop()
        api.delete_endpoint.assert_awaited_once_with("ep-basic")
        assert session._endpoint_ids == []


class TestDevEvents:
    async def test_lifecycle_events_emitted(self):
        events = []

        class Sink:
            def provisioning(self, name, kind, hardware):
                events.append(("provisioning", name, kind, hardware))

            def adopted(self, name, endpoint_id):
                events.append(("adopted", name, endpoint_id))

            def ready(self, name, endpoint_id):
                events.append(("ready", name, endpoint_id))

            def resource_changed(self, name, fields):
                events.append(("resource_changed", name, fields))

            def resource_added(self, name, kind, hardware):
                events.append(("resource_added", name, kind, hardware))

            def resource_removed(self, name):
                events.append(("resource_removed", name))

            def deleted(self, name):
                events.append(("deleted", name))

        app = App("ev-app")

        @app.queue(name="q-basic", cpu="cpu3c-1-2")
        def q():
            pass

        api = AsyncMock()
        api.list_my_endpoints.return_value = []
        api.save_endpoint.return_value = {"id": "ep-1"}
        api.delete_endpoint.return_value = True

        session = DevSession([app], api=api, events=Sink())
        await session.start()
        await session.refresh([app])
        await session.stop()

        kinds = [e[0] for e in events]
        assert "provisioning" in kinds
        assert "ready" in kinds
        assert "deleted" in kinds
        # unchanged resource refreshes silently: no diff events
        assert "resource_changed" not in kinds
        assert "resource_added" not in kinds
        assert ("provisioning", "q-basic", "queue", "cpu3c-1-2") in events
        # deleted reports the resource name, not the endpoint name
        assert ("deleted", "q-basic") in events

    async def test_refresh_diff_events(self):
        events = []

        class Sink:
            def resource_added(self, name, kind, hardware):
                events.append(("added", name, kind, hardware))

            def resource_changed(self, name, fields):
                events.append(("changed", name, tuple(fields)))

            def resource_removed(self, name):
                events.append(("removed", name))

        app = App("diff-app")

        @app.queue(name="stays", cpu="cpu3c-1-2")
        def stays():
            pass

        @app.queue(name="goes-away", cpu="cpu3c-1-2")
        def goes():
            pass

        api = AsyncMock()
        api.list_my_endpoints.return_value = []
        api.save_endpoint.side_effect = lambda p: {"id": f"ep-{p['name']}"}
        api.delete_endpoint.return_value = True

        session = DevSession([app], api=api, events=Sink())
        await session.start()

        # rescan: 'goes' removed, 'stays' reconfigured, 'fresh' added
        app2 = App("diff-app")

        @app2.queue(name="stays", cpu="cpu3c-1-2", workers=(1, 4))
        def stays2():
            pass

        @app2.queue(name="fresh", cpu="cpu5c-2-4")
        def fresh():
            pass

        await session.refresh([app2])

        assert ("removed", "goes-away") in events
        added = [e for e in events if e[0] == "added"]
        assert ("added", "fresh", "queue", "cpu5c-2-4") in added
        changed = [e for e in events if e[0] == "changed"]
        assert len(changed) == 1
        assert changed[0][1] == "stays"
        assert "workersMin" in changed[0][2] or "workersMax" in changed[0][2]

    async def test_missing_event_methods_ignored(self):
        app = App("ev-app2")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        api = AsyncMock()
        api.list_my_endpoints.return_value = []
        api.save_endpoint.return_value = {"id": "ep-1"}

        # events object with no handlers at all must not break anything
        session = DevSession([app], api=api, events=object())
        await session.start()
        await session.stop()


class TestGpuIdsPayload:
    def test_no_gpu_selection_expands_to_all_pools(self):
        # the api rejects "any"; an unconstrained gpu queue asks for
        # every pool id instead
        app = App("gpu-any")

        @app.queue(name="q")
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        gpu_ids = payload["gpuIds"].split(",")
        assert "any" not in gpu_ids
        assert "ADA_24" in gpu_ids and "AMPERE_80" in gpu_ids

    def test_explicit_pools_pass_through(self):
        app = App("gpu-explicit")

        @app.queue(name="q", gpu="ADA_24")
        def q():
            pass

        payload = _endpoint_input(app, q.spec)
        assert payload["gpuIds"] == "ADA_24"


class TestRefreshKeepsTaskEvents:
    async def test_dev_events_reattached_after_refresh(self):
        sink = object()
        app = App("evt-app")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        api = AsyncMock()
        api.list_my_endpoints.return_value = []
        api.save_endpoint.return_value = {"id": "ep-1"}

        session = DevSession([app], api=api, events=sink)
        await session.start()
        assert app._dev_events is sink

        # file change: discovery builds a brand-new app instance
        app2 = App("evt-app")

        @app2.queue(name="q", cpu="cpu3c-1-2")
        def q2():
            pass

        await session.refresh([app2])
        assert app2._dev_events is sink
