"""unit tests for the apps control-plane client."""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from runpod.apps.api import AppsApiClient
from runpod.error import QueryError


def _respond(data):
    return AsyncMock(return_value={"data": data})


def _client_with(data):
    client = AppsApiClient(api_key="test-key")
    return client, patch(
        "runpod.apps.api.run_graphql_query_async", _respond(data)
    )


class TestExecuteRetry:
    async def test_returns_data(self):
        client, patcher = _client_with({"ok": 1})
        with patcher:
            assert await client._execute("query {}") == {"ok": 1}

    async def test_retries_transport_errors(self):
        client = AppsApiClient(api_key="test-key")
        transport = AsyncMock(
            side_effect=[
                aiohttp.ClientError("reset"),
                OSError("dns"),
                {"data": {"ok": 1}},
            ]
        )
        with (
            patch("runpod.apps.api.run_graphql_query_async", transport),
            patch("asyncio.sleep", AsyncMock()),
        ):
            assert await client._execute("query {}", retry=True) == {"ok": 1}
        assert transport.await_count == 3

    async def test_exhausted_retries_raise(self):
        client = AppsApiClient(api_key="test-key")
        transport = AsyncMock(side_effect=aiohttp.ClientError("down"))
        with (
            patch("runpod.apps.api.run_graphql_query_async", transport),
            patch("asyncio.sleep", AsyncMock()),
        ):
            with pytest.raises(aiohttp.ClientError):
                await client._execute("query {}", retry=True)
        assert transport.await_count == 4

    async def test_graphql_errors_propagate_immediately(self):
        client = AppsApiClient(api_key="test-key")
        transport = AsyncMock(side_effect=QueryError("bad query", "query {}"))
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            with pytest.raises(QueryError):
                await client._execute("query {}")
        assert transport.await_count == 1

    @pytest.mark.parametrize(
        "failure",
        [aiohttp.ClientError("response lost"), OSError("reset")],
    )
    async def test_mutation_transport_failure_is_not_retried(self, failure):
        client = AppsApiClient(api_key="test-key")
        transport = AsyncMock(side_effect=failure)
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            with pytest.raises(type(failure)):
                await client.create_app("demo")
        assert transport.await_count == 1


class TestEndpoints:
    async def test_dev_endpoint_preserves_runtime_and_placement(self, monkeypatch):
        from runpod.apps import App
        from runpod.apps.dev import _endpoint_input
        from runpod.apps.spec import ResourceKind, ResourceSpec
        monkeypatch.setattr("runpod.apps.app._REGISTRY", [])

        spec = ResourceSpec(
            kind=ResourceKind.QUEUE, name="chat", gpu="4090", gpu_count=2,
            datacenter="EU-RO-1", workers=(1, 3), idle_timeout=60,
        )
        payload = _endpoint_input(App("demo"), spec)
        payload["template"]["containerRegistryAuthId"] = "registry1"
        payload["networkVolumeIds"] = [{"networkVolumeId": "volume1"}]

        async def create(method, path, *, api_key, json):
            assert (method, path) == ("POST", "/v2/serverless")
            assert json["gpu"]["count"] == 2
            assert json["dataCenterIds"] == ["EU-RO-1"]
            assert json["networkVolumes"] == ["volume1"]
            assert json["registry"] == "registry1"
            assert json["workers"] == {"min": 1, "max": 3, "idleTimeout": 60}
            assert json["env"]["RUNPOD_DEV_RESOURCE"] == "chat"
            assert "FLASH_RESOURCE_NAME" not in json["env"]
            assert "RUNPOD_RESOURCE_NAME" not in json["env"]
            return {"id": "ep1", "name": json["name"]}

        client = AppsApiClient(api_key="test-key")
        with (
            patch("runpod.apps.api.run_rest_request_async", side_effect=create),
            patch("runpod.apps.api.run_graphql_query_async", AsyncMock()) as graphql,
        ):
            assert (await client.save_endpoint(payload))["id"] == "ep1"
        graphql.assert_not_awaited()

    @pytest.mark.parametrize("capability", [
        {"flashEnvironmentId": "env1"},
        {"modelReferences": [{"name": "model"}]},
        {"schedule": {"cron": "* * * * *"}},
        {"template": {"name": "user-template", "imageName": "image"}},
        {"instanceIds": ["cpu3c-1-2"]},
        {"instanceIds": ["cpu3c-2-4", "cpu3c-1-2"]},
    ])
    async def test_unsupported_capability_remains_one_atomic_graphql_save(self, capability):
        payload = {
            "name": "chat", "template": {"imageName": "image"},
            "scalerType": "QUEUE_DELAY", "scalerValue": 4,
            **capability,
        }
        client = AppsApiClient(api_key="test-key")
        graphql = _respond({"saveEndpoint": {"id": "ep1"}})
        with (
            patch("runpod.apps.api.run_graphql_query_async", graphql),
            patch("runpod.apps.api.run_rest_request_async", AsyncMock()) as rest,
        ):
            await client.save_endpoint(payload)
        rest.assert_not_awaited()
        expected_template = {"name": payload["name"], **payload["template"]}
        assert graphql.call_args.kwargs["variables"]["input"] == {
            **payload, "template": expected_template,
        }
        assert payload["template"] == capability.get("template", {"imageName": "image"})

    @pytest.mark.parametrize("routing", ["QB", "LB"])
    async def test_request_count_preserves_only_effective_idle_timeout(self, routing):
        client = AppsApiClient(api_key="test-key")
        transport = AsyncMock(return_value={"id": "ep1"})
        with patch("runpod.apps.api.run_rest_request_async", transport):
            await client.save_endpoint({
                "name": "chat", "type": routing, "template": {"imageName": "image"},
                "scalerType": "REQUEST_COUNT", "scalerValue": 7, "idleTimeout": 60,
                "workersMin": 0, "workersMax": 2,
            })
        body = transport.call_args.kwargs["json"]
        assert body["scaling"] == {"type": "REQUEST_COUNT", "requestCount": 7}
        assert body["workers"].get("idleTimeout") == (60 if routing == "LB" else None)

    async def test_rest_update_preserves_cpu_alternatives_and_load_balancer_idle_timeout(self):
        client = AppsApiClient(api_key="test-key")
        rest = AsyncMock(side_effect=[
            {"id": "ep1", "type": "LOAD_BALANCER", "cpu": [{"id": "cpu5c"}]},
            {"id": "ep1"},
        ])
        with (
            patch("runpod.apps.api.run_rest_request_async", rest),
            patch("runpod.apps.api.run_graphql_query_async", AsyncMock()) as graphql,
        ):
            await client.save_endpoint({
                "id": "ep1", "type": "LB",
                "instanceIds": ["cpu5c-2-4", "cpu5g-4-16"],
                "scalerType": "REQUEST_COUNT", "scalerValue": 7, "idleTimeout": 60,
                "template": {"env": [{"key": "RUNPOD_DEV_GENERATION", "value": "2"}]},
            })
        patch_request = rest.call_args
        assert patch_request.args == ("PATCH", "/v2/serverless/ep1")
        body = patch_request.kwargs["json"]
        assert body["cpu"] == [
            {"id": "cpu5c", "vcpuCount": 2}, {"id": "cpu5g", "vcpuCount": 4},
        ]
        assert body["workers"]["idleTimeout"] == 60
        assert body["env"] == {"RUNPOD_DEV_GENERATION": "2"}
        graphql.assert_not_awaited()

    async def test_routing_change_remains_atomic_graphql_operation(self):
        client = AppsApiClient(api_key="test-key")
        rest = AsyncMock(return_value={"id": "ep1", "type": "QUEUE"})
        graphql = _respond({"saveEndpoint": {"id": "ep1"}})
        with (
            patch("runpod.apps.api.run_rest_request_async", rest),
            patch("runpod.apps.api.run_graphql_query_async", graphql),
        ):
            await client.save_endpoint({"id": "ep1", "type": "LB"})
        assert rest.call_args.args == ("GET", "/v2/serverless/ep1")
        assert rest.await_count == 1
        assert graphql.call_args.kwargs["variables"]["input"]["type"] == "LB"

    @pytest.mark.parametrize("failure", [
        aiohttp.ClientError("response lost"), QueryError("denied", status_code=403),
    ])
    async def test_rest_save_failure_never_retries_or_falls_back(self, failure):
        rest = AsyncMock(side_effect=failure)
        client = AppsApiClient(api_key="test-key")
        with (
            patch("runpod.apps.api.run_rest_request_async", rest),
            patch("runpod.apps.api.run_graphql_query_async", AsyncMock()) as graphql,
            pytest.raises(type(failure)),
        ):
            await client.save_endpoint({
                "name": "chat", "template": {"imageName": "image"},
                "scalerType": "QUEUE_DELAY", "scalerValue": 4,
            })
        assert rest.await_count == 1
        graphql.assert_not_awaited()

    @pytest.mark.parametrize("method", ["delete_endpoint", "delete_registry_auth", "terminate_pod"])
    async def test_delete_failure_is_not_reported_as_success(self, method):
        client = AppsApiClient(api_key="test-key")
        rest = AsyncMock(side_effect=QueryError("denied", status_code=403))
        with (
            patch("runpod.apps.api.run_rest_request_async", rest),
            patch("runpod.apps.api.run_graphql_query_async", AsyncMock()) as graphql,
            pytest.raises(QueryError),
        ):
            await getattr(client, method)("resource1")
        assert rest.await_count == 1
        graphql.assert_not_awaited()


class TestTaskPods:
    async def test_deploy_gpu_pod(self):
        client = AppsApiClient(api_key="test-key")
        transport = _respond(
            {"podFindAndDeployOnDemand": {"id": "pod1", "desiredStatus": "RUNNING"}}
        )
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            result = await client.deploy_task_pod(
                {
                    "gpuTypeIdList": ["NVIDIA GeForce RTX 4090"],
                    "terminateAfter": "2026-09-15T12:30:00Z",
                    "supportPublicIp": True,
                }, is_cpu=False
            )
        assert result["id"] == "pod1"
        sent = transport.call_args.kwargs["variables"]["input"]
        assert sent["terminateAfter"] == "2026-09-15T12:30:00Z"
        assert sent["supportPublicIp"] is True

    async def test_cpu_alternative_can_deploy_in_volume_datacenter(self):
        client = AppsApiClient(api_key="test-key")
        transport = AsyncMock(
            side_effect=[
                QueryError("insufficient capacity"),
                {"data": {"deployCpuPod": {"id": "pod2"}}},
            ]
        )
        pod = {
            "instanceIds": ["cpu3c-2-4", "cpu3g-2-8"],
            "dataCenterIds": ["US-CA-2"],
            "networkVolumeId": "volume1",
        }
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            result = await client.deploy_task_pod(pod, is_cpu=True)
        assert result["id"] == "pod2"
        requests = [
            call.kwargs["variables"]["input"] for call in transport.await_args_list
        ]
        assert [request["instanceId"] for request in requests] == pod["instanceIds"]
        assert all(request["dataCenterIds"] == ["US-CA-2"] for request in requests)
        assert all(request["networkVolumeId"] == "volume1" for request in requests)

    @pytest.mark.parametrize(
        "failure", [QueryError("invalid image"), aiohttp.ClientError("response lost")]
    )
    async def test_cpu_alternatives_stop_after_ambiguous_or_invalid_failure(self, failure):
        client = AppsApiClient(api_key="test-key")
        transport = AsyncMock(side_effect=failure)
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            with pytest.raises(type(failure)):
                await client.deploy_task_pod(
                    {"instanceIds": ["cpu3c-2-4", "cpu3g-2-8"]}, is_cpu=True
                )
        assert transport.await_count == 1


class TestAppLifecycle:
    async def test_get_app_by_name(self):
        client, patcher = _client_with(
            {"flashAppByName": {"id": "app1", "name": "demo"}}
        )
        with patcher:
            app = await client.get_app_by_name("demo")
        assert app["id"] == "app1"

    async def test_create_app(self):
        client, patcher = _client_with(
            {"createFlashApp": {"id": "app1", "name": "demo"}}
        )
        with patcher:
            assert (await client.create_app("demo"))["id"] == "app1"

    async def test_create_environment(self):
        client, patcher = _client_with(
            {"createFlashEnvironment": {"id": "env1", "name": "prod"}}
        )
        with patcher:
            result = await client.create_environment("app1", "prod")
        assert result["id"] == "env1"

    async def test_list_apps(self):
        client, patcher = _client_with(
            {"myself": {"flashApps": [{"id": "app1"}]}}
        )
        with patcher:
            assert await client.list_apps() == [{"id": "app1"}]

    async def test_list_apps_empty(self):
        client, patcher = _client_with({"myself": {"flashApps": None}})
        with patcher:
            assert await client.list_apps() == []

    async def test_delete_app(self):
        client, patcher = _client_with({"deleteFlashApp": True})
        with patcher:
            assert await client.delete_app("app1") is True

    async def test_delete_environment(self):
        client, patcher = _client_with({"deleteFlashEnvironment": True})
        with patcher:
            assert await client.delete_environment("env1") is True

    async def test_get_environment_by_name(self):
        client = AppsApiClient(api_key="test-key")
        transport = AsyncMock(
            side_effect=[
                {"data": {"flashAppByName": {"id": "app1"}}},
                {"data": {"flashEnvironmentByName": {"id": "env1"}}},
            ]
        )
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            env = await client.get_environment_by_name("demo", "prod")
        assert env["id"] == "env1"

    async def test_get_environment_missing_app(self):
        client, patcher = _client_with({"flashAppByName": None})
        with patcher:
            assert await client.get_environment_by_name("demo", "prod") is None

    async def test_get_environment_not_found(self):
        client = AppsApiClient(api_key="test-key")
        transport = AsyncMock(
            side_effect=[
                {"data": {"flashAppByName": {"id": "app1"}}},
                QueryError("environment not found", "query {}"),
            ]
        )
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            assert await client.get_environment_by_name("demo", "prod") is None


class TestStock:
    async def test_gpu_availability_respects_count_product_and_datacenter(self):
        async def catalog(method, path, *, api_key, params):
            available = (
                params["product"] == "SERVERLESS" and params["count"] == 1
                and params["cloud"] == "SECURE" and params["include"] == "AVAILABILITY"
            )
            return {"dataCenters": [
                {"id": "EU-RO-1", "availability": "HIGH" if available else "LOW"}
            ]}

        client = AppsApiClient(api_key="test-key")
        with patch("runpod.apps.api.run_rest_request_async", side_effect=catalog):
            assert await client.gpu_stock_status("4090", "EU-RO-1") == "HIGH"
            assert await client.gpu_stock_status("4090", "EU-RO-1", 2) == "LOW"
            assert await client.gpu_stock_status("4090", "EU-RO-1", pods=True) == "LOW"
            assert await client.gpu_stock_status("4090", "US-KS-2") == "NONE"

    async def test_cpu_availability_respects_flavor_size_and_product(self):
        async def catalog(method, path, *, api_key, params):
            available = (
                path == "/v2/catalog/cpus/cpu3c" and params["vcpuCount"] == 2
                and params["product"] == "POD" and params["include"] == "AVAILABILITY"
            )
            return {"dataCenters": [
                {"id": "EU-RO-1", "availability": "MEDIUM" if available else "NONE"}
            ]}

        client = AppsApiClient(api_key="test-key")
        with patch("runpod.apps.api.run_rest_request_async", side_effect=catalog):
            assert await client.cpu_stock_status("cpu3c-2-4", "EU-RO-1", pods=True) == "MEDIUM"
            assert await client.cpu_stock_status("cpu3c-4-8", "EU-RO-1", pods=True) == "NONE"
            assert await client.cpu_stock_status("cpu3c-2-4", "EU-RO-1") == "NONE"

    @pytest.mark.parametrize("pods", [False, True])
    @pytest.mark.parametrize("instance_id", ["cpu3c-1-2", "cpu3c-3-6"])
    async def test_legacy_cpu_stock_keeps_exact_configuration(self, pods, instance_id):
        async def graphql(query, *, api_key, variables, anonymous):
            flavor = variables["cpuFlavorInput"]
            specifics = variables["specificsInput"]
            available = (
                flavor == {"id": "cpu3c", "isSls": not pods}
                and specifics == {
                    "dataCenterId": "EU-RO-1", "instanceId": instance_id,
                    "isSls": not pods,
                }
            )
            return {"data": {"cpuFlavors": [
                {"specifics": {"stockStatus": "High" if available else "None"}}
            ]}}

        client = AppsApiClient(api_key="test-key")
        with (
            patch("runpod.apps.api.run_graphql_query_async", side_effect=graphql),
            patch("runpod.apps.api.run_rest_request_async", AsyncMock()) as rest,
        ):
            assert await client.cpu_stock_status(instance_id, "EU-RO-1", pods=pods) == "High"
        rest.assert_not_awaited()


class TestVolumesRegistrySecrets:
    async def test_secret_crud(self):
        client, patcher = _client_with(
            {
                "myself": {"secrets": [{"id": "s1"}]},
                "secretCreate": {"id": "s1", "name": "tok"},
                "secretDelete": True,
            }
        )
        with patcher:
            assert await client.list_secrets() == [{"id": "s1"}]
            assert (await client.create_secret("tok", "v"))["id"] == "s1"
            assert await client.delete_secret("s1") is True


class TestAuthRequests:
    async def test_create_auth_request_is_anonymous(self):
        client = AppsApiClient(api_key="test-key")
        transport = _respond(
            {"createFlashAuthRequest": {"id": "req1", "status": "PENDING"}}
        )
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            result = await client.create_auth_request()
        assert result["id"] == "req1"
        assert transport.call_args[1]["anonymous"] is True

    async def test_status_poll_is_anonymous(self):
        client = AppsApiClient(api_key="test-key")
        transport = _respond(
            {"flashAuthRequestStatus": {"id": "req1", "status": "APPROVED"}}
        )
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            result = await client.get_auth_request_status("req1")
        assert result["status"] == "APPROVED"
        assert transport.call_args[1]["anonymous"] is True


class TestArtifacts:
    async def test_prepare_artifact_upload(self):
        client, patcher = _client_with(
            {
                "prepareFlashArtifactUpload": {
                    "uploadUrl": "https://s3",
                    "objectKey": "k",
                }
            }
        )
        with patcher:
            result = await client.prepare_artifact_upload("app1", 123)
        assert result["objectKey"] == "k"

    async def test_finalize_artifact_upload(self):
        client, patcher = _client_with(
            {"finalizeFlashArtifactUpload": {"id": "b1", "manifest": {}}}
        )
        with patcher:
            result = await client.finalize_artifact_upload("app1", "k", {})
        assert result["id"] == "b1"

    async def test_deploy_build(self):
        client, patcher = _client_with(
            {"deployBuildToEnvironment": {"id": "env1", "name": "prod"}}
        )
        with patcher:
            result = await client.deploy_build("env1", "b1")
        assert result["id"] == "env1"


class TestUploadTarball:
    async def test_upload_reports_progress(self, tmp_path):
        tar = tmp_path / "app.tar.gz"
        tar.write_bytes(b"x" * 2048)
        client = AppsApiClient(api_key="test-key")
        progress = MagicMock()

        put = AsyncMock()
        with patch.object(client, "_put_tarball", put):
            await client.upload_tarball("https://s3", str(tar), progress)
        put.assert_awaited_once()

        # drain the reader to drive progress callbacks
        reader = put.call_args[0][1]
        async for _ in reader:
            pass
        progress.assert_called_with(2048, 2048)

    async def test_upload_retries_then_succeeds(self, tmp_path):
        tar = tmp_path / "app.tar.gz"
        tar.write_bytes(b"x")
        client = AppsApiClient(api_key="test-key")

        put = AsyncMock(side_effect=[OSError("broken pipe"), None])
        with (
            patch.object(client, "_put_tarball", put),
            patch("asyncio.sleep", AsyncMock()),
        ):
            await client.upload_tarball("https://s3", str(tar))
        assert put.await_count == 2

    async def test_upload_exhausted_raises(self, tmp_path):
        tar = tmp_path / "app.tar.gz"
        tar.write_bytes(b"x")
        client = AppsApiClient(api_key="test-key")

        put = AsyncMock(side_effect=aiohttp.ClientError("reset"))
        with (
            patch.object(client, "_put_tarball", put),
            patch("asyncio.sleep", AsyncMock()),
        ):
            with pytest.raises(aiohttp.ClientError):
                await client.upload_tarball("https://s3", str(tar))
        assert put.await_count == 4
