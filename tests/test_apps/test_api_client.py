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
            assert await client._execute("query {}") == {"ok": 1}
        assert transport.await_count == 3

    async def test_exhausted_retries_raise(self):
        client = AppsApiClient(api_key="test-key")
        transport = AsyncMock(side_effect=aiohttp.ClientError("down"))
        with (
            patch("runpod.apps.api.run_graphql_query_async", transport),
            patch("asyncio.sleep", AsyncMock()),
        ):
            with pytest.raises(aiohttp.ClientError):
                await client._execute("query {}")
        assert transport.await_count == 4

    async def test_graphql_errors_propagate_immediately(self):
        client = AppsApiClient(api_key="test-key")
        transport = AsyncMock(side_effect=QueryError("bad query", "query {}"))
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            with pytest.raises(QueryError):
                await client._execute("query {}")
        assert transport.await_count == 1


class TestEndpoints:
    async def test_save_endpoint(self):
        client, patcher = _client_with(
            {"saveEndpoint": {"id": "ep1", "name": "chat"}}
        )
        with patcher:
            result = await client.save_endpoint({"name": "chat"})
        assert result["id"] == "ep1"

    async def test_delete_endpoint(self):
        client, patcher = _client_with({"deleteEndpoint": True})
        with patcher:
            assert await client.delete_endpoint("ep1") is True

    async def test_list_my_endpoints(self):
        client, patcher = _client_with(
            {"myself": {"endpoints": [{"id": "ep1"}]}}
        )
        with patcher:
            assert await client.list_my_endpoints() == [{"id": "ep1"}]


class TestTaskPods:
    async def test_deploy_gpu_pod(self):
        client = AppsApiClient(api_key="test-key")
        transport = _respond(
            {"podFindAndDeployOnDemand": {"id": "pod1", "desiredStatus": "RUNNING"}}
        )
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            result = await client.deploy_task_pod(
                {"gpuTypeIdList": ["NVIDIA GeForce RTX 4090"]}, is_cpu=False
            )
        assert result["id"] == "pod1"

    async def test_deploy_cpu_pod_converts_instance_ids(self):
        client = AppsApiClient(api_key="test-key")
        transport = _respond({"deployCpuPod": {"id": "pod2"}})
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            result = await client.deploy_task_pod(
                {"instanceIds": ["cpu3c-2-4", "cpu3g-2-8"]}, is_cpu=True
            )
        assert result["id"] == "pod2"
        sent = transport.call_args[1]["variables"]["input"]
        assert sent["instanceId"] == "cpu3c-2-4"
        assert "instanceIds" not in sent

    async def test_terminate_pod(self):
        client = AppsApiClient(api_key="test-key")
        transport = _respond({"podTerminate": None})
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            await client.terminate_pod("pod1")
        sent = transport.call_args[1]["variables"]["input"]
        assert sent == {"podId": "pod1"}


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
    async def test_gpu_stock_status(self):
        client, patcher = _client_with(
            {"gpuTypes": [{"lowestPrice": {"stockStatus": "High"}}]}
        )
        with patcher:
            status = await client.gpu_stock_status(
                "NVIDIA GeForce RTX 4090", "US-KS-2"
            )
        assert status == "High"

    async def test_gpu_stock_no_data(self):
        client, patcher = _client_with({"gpuTypes": []})
        with patcher:
            assert (
                await client.gpu_stock_status("X", "US-KS-2") is None
            )

    async def test_gpu_stock_pods_flag(self):
        client = AppsApiClient(api_key="test-key")
        transport = _respond({"gpuTypes": []})
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            await client.gpu_stock_status("X", "US-KS-2", pods=True)
        sent = transport.call_args[1]["variables"]["lowestPriceInput"]
        assert sent["includeAiApi"] is False

    async def test_cpu_stock_status(self):
        client = AppsApiClient(api_key="test-key")
        transport = _respond(
            {"cpuFlavors": [{"specifics": {"stockStatus": "Low"}}]}
        )
        with patch("runpod.apps.api.run_graphql_query_async", transport):
            status = await client.cpu_stock_status("cpu3c-2-4", "US-KS-2")
        assert status == "Low"
        sent = transport.call_args[1]["variables"]
        assert sent["cpuFlavorInput"] == {"id": "cpu3c"}


class TestVolumesRegistrySecrets:
    async def test_list_network_volumes(self):
        client, patcher = _client_with(
            {"myself": {"networkVolumes": [{"id": "v1"}]}}
        )
        with patcher:
            assert await client.list_network_volumes() == [{"id": "v1"}]

    async def test_create_network_volume(self):
        client, patcher = _client_with(
            {"createNetworkVolume": {"id": "v1", "name": "data"}}
        )
        with patcher:
            result = await client.create_network_volume("data", 10, "US-KS-2")
        assert result["id"] == "v1"

    async def test_registry_auth_crud(self):
        client, patcher = _client_with(
            {
                "myself": {"containerRegistryCreds": [{"id": "r1"}]},
                "saveRegistryAuth": {"id": "r1", "name": "dh"},
                "deleteRegistryAuth": True,
            }
        )
        with patcher:
            assert await client.list_registry_auths() == [{"id": "r1"}]
            assert (
                await client.create_registry_auth("dh", "user", "pass")
            )["id"] == "r1"
            assert await client.delete_registry_auth("r1") is True

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
