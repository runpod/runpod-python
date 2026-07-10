"""control-plane calls for app provisioning.

a thin wrapper over runpod.api.graphql's shared async transport,
scoped to what the apps surface needs: endpoint save/delete for dev
sessions, and the app / build / environment lifecycle for deploys.
management verbs for the wider sdk stay in runpod.api.ctl_commands.
"""

from typing import Any, Dict, List, Optional

import aiohttp

from ..api.graphql import run_graphql_query_async
from ..api.mutations import apps as app_mutations
from ..api.queries import apps as app_queries
from ..error import QueryError


_TRANSPORT_RETRIES = 4


class AppsApiClient:
    """async control-plane client scoped to apps provisioning."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key

    async def _execute(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        *,
        anonymous: bool = False,
    ) -> Dict[str, Any]:
        import asyncio

        last_exc: Optional[Exception] = None
        for attempt in range(_TRANSPORT_RETRIES):
            if attempt:
                await asyncio.sleep(2 ** (attempt - 1))
            try:
                response = await run_graphql_query_async(
                    query,
                    api_key=self._api_key,
                    variables=variables,
                    anonymous=anonymous,
                )
                return response["data"]
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
                # transport-level failures (ssl hiccups, resets, dns)
                # are transient; graphql/auth errors propagate untouched
                last_exc = exc
        if last_exc is None:  # pragma: no cover - loop always runs once
            raise RuntimeError("graphql transport retry loop exited cleanly")
        raise last_exc

    async def save_endpoint(self, endpoint_input: Dict[str, Any]) -> Dict[str, Any]:
        """create or update a serverless endpoint. include id to update."""
        mutation = app_mutations.MUTATION_SAVE_ENDPOINT
        data = await self._execute(mutation, {"input": endpoint_input})
        return data["saveEndpoint"]

    async def delete_endpoint(self, endpoint_id: str) -> bool:
        mutation = app_mutations.MUTATION_DELETE_ENDPOINT
        data = await self._execute(mutation, {"id": endpoint_id})
        return bool(data.get("deleteEndpoint"))

    async def list_my_endpoints(self) -> List[Dict[str, Any]]:
        query = app_queries.QUERY_MY_ENDPOINTS
        data = await self._execute(query)
        return data["myself"]["endpoints"]

    async def deploy_task_pod(
        self, pod_input: Dict[str, Any], *, is_cpu: bool
    ) -> Dict[str, Any]:
        """deploy an on-demand pod for a task run."""
        pod_input = dict(pod_input)
        if is_cpu:
            # deployCpuPod takes a single instanceId
            instance_ids = pod_input.pop("instanceIds", None)
            if instance_ids:
                pod_input["instanceId"] = instance_ids[0]
            mutation = app_mutations.MUTATION_DEPLOY_CPU_POD
            data = await self._execute(mutation, {"input": pod_input})
            return data["deployCpuPod"]

        mutation = app_mutations.MUTATION_DEPLOY_POD
        data = await self._execute(mutation, {"input": pod_input})
        return data["podFindAndDeployOnDemand"]

    async def terminate_pod(self, pod_id: str) -> None:
        mutation = app_mutations.MUTATION_TERMINATE_POD
        await self._execute(mutation, {"input": {"podId": pod_id}})

    async def get_app_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        query = app_queries.QUERY_FLASH_APP_BY_NAME
        try:
            data = await self._execute(query, {"flashAppName": name})
        except QueryError as exc:
            if "not found" in str(exc).lower():
                return None
            raise
        return data["flashAppByName"]

    async def create_app(self, name: str) -> Dict[str, Any]:
        mutation = app_mutations.MUTATION_CREATE_FLASH_APP
        data = await self._execute(mutation, {"input": {"name": name}})
        return data["createFlashApp"]

    async def create_environment(self, app_id: str, name: str) -> Dict[str, Any]:
        mutation = app_mutations.MUTATION_CREATE_FLASH_ENVIRONMENT
        data = await self._execute(
            mutation, {"input": {"flashAppId": app_id, "name": name}}
        )
        return data["createFlashEnvironment"]

    async def gpu_stock_status(
        self,
        gpu_id: str,
        data_center_id: str,
        gpu_count: int = 1,
        pods: bool = False,
    ) -> Optional[str]:
        """stock signal for a gpu device in one datacenter.

        the serverless plane (includeAiApi) and the pod plane have
        different availability; pods=True queries pod stock (tasks).
        """
        query = app_queries.QUERY_GPU_STOCK
        data = await self._execute(
            query,
            {
                "gpuTypesInput": {"ids": [gpu_id]},
                "lowestPriceInput": {
                    "dataCenterId": data_center_id,
                    "gpuCount": gpu_count,
                    "secureCloud": True,
                    "includeAiApi": not pods,
                },
            },
        )
        gpu_types = data.get("gpuTypes") or []
        first = gpu_types[0] if gpu_types else {}
        price = first.get("lowestPrice") if isinstance(first, dict) else None
        return (price or {}).get("stockStatus")

    async def cpu_stock_status(
        self, instance_id: str, data_center_id: str
    ) -> Optional[str]:
        """stock signal for a cpu flavor in one datacenter."""
        flavor = instance_id.split("-", 1)[0]
        query = app_queries.QUERY_CPU_STOCK
        data = await self._execute(
            query,
            {
                "cpuFlavorInput": {"id": flavor},
                "specificsInput": {
                    "dataCenterId": data_center_id,
                    "instanceId": instance_id,
                },
            },
        )
        flavors = data.get("cpuFlavors") or []
        first = flavors[0] if flavors else {}
        specifics = first.get("specifics") if isinstance(first, dict) else None
        return (specifics or {}).get("stockStatus")

    async def list_network_volumes(self) -> List[Dict[str, Any]]:
        query = app_queries.QUERY_NETWORK_VOLUMES
        data = await self._execute(query)
        return data["myself"].get("networkVolumes") or []

    async def create_network_volume(
        self, name: str, size: int, data_center_id: str
    ) -> Dict[str, Any]:
        mutation = app_mutations.MUTATION_CREATE_NETWORK_VOLUME
        data = await self._execute(
            mutation,
            {
                "input": {
                    "name": name,
                    "size": size,
                    "dataCenterId": data_center_id,
                }
            },
        )
        return data["createNetworkVolume"]

    async def list_registry_auths(self) -> List[Dict[str, Any]]:
        query = app_queries.QUERY_REGISTRY_AUTHS
        data = await self._execute(query)
        return data["myself"].get("containerRegistryCreds") or []

    async def create_registry_auth(
        self, name: str, username: str, password: str
    ) -> Dict[str, Any]:
        mutation = app_mutations.MUTATION_SAVE_REGISTRY_AUTH
        data = await self._execute(
            mutation,
            {
                "input": {
                    "name": name,
                    "username": username,
                    "password": password,
                }
            },
        )
        return data["saveRegistryAuth"]

    async def delete_registry_auth(self, auth_id: str) -> bool:
        mutation = app_mutations.MUTATION_DELETE_REGISTRY_AUTH
        data = await self._execute(mutation, {"registryAuthId": auth_id})
        return bool(data.get("deleteRegistryAuth"))

    async def list_secrets(self) -> List[Dict[str, Any]]:
        query = app_queries.QUERY_SECRETS
        data = await self._execute(query)
        return data["myself"].get("secrets") or []

    async def create_secret(
        self, name: str, value: str, description: str = ""
    ) -> Dict[str, Any]:
        mutation = app_mutations.MUTATION_CREATE_SECRET
        data = await self._execute(
            mutation,
            {
                "input": {
                    "name": name,
                    "value": value,
                    "description": description,
                }
            },
        )
        return data["secretCreate"]

    async def delete_secret(self, secret_id: str) -> bool:
        mutation = app_mutations.MUTATION_DELETE_SECRET
        data = await self._execute(mutation, {"id": secret_id})
        return bool(data.get("secretDelete"))

    async def list_apps(self) -> List[Dict[str, Any]]:
        """all flash apps with their environments and builds."""
        query = app_queries.QUERY_FLASH_APPS
        data = await self._execute(query)
        return data["myself"].get("flashApps") or []

    async def get_environment_by_name(
        self, app_name: str, env_name: str
    ) -> Optional[Dict[str, Any]]:
        """an environment with its attached endpoints and volumes."""
        app = await self.get_app_by_name(app_name)
        if app is None:
            return None
        query = app_queries.QUERY_FLASH_ENVIRONMENT_BY_NAME
        try:
            data = await self._execute(
                query,
                {"input": {"flashAppId": app["id"], "name": env_name}},
            )
        except QueryError as exc:
            if "not found" in str(exc).lower():
                return None
            raise
        return data["flashEnvironmentByName"]

    async def delete_app(self, app_id: str) -> bool:
        mutation = app_mutations.MUTATION_DELETE_FLASH_APP
        data = await self._execute(mutation, {"flashAppId": app_id})
        return bool(data.get("deleteFlashApp"))

    async def delete_environment(self, environment_id: str) -> bool:
        mutation = app_mutations.MUTATION_DELETE_FLASH_ENVIRONMENT
        data = await self._execute(mutation, {"flashEnvironmentId": environment_id})
        return bool(data.get("deleteFlashEnvironment"))

    async def create_auth_request(self) -> Dict[str, Any]:
        """open a browser-approval auth request; no credentials needed."""
        mutation = app_mutations.MUTATION_CREATE_FLASH_AUTH_REQUEST
        data = await self._execute(mutation, anonymous=True)
        return data.get("createFlashAuthRequest") or {}

    async def get_auth_request_status(self, request_id: str) -> Dict[str, Any]:
        query = app_queries.QUERY_FLASH_AUTH_REQUEST_STATUS
        data = await self._execute(
            query,
            {"flashAuthRequestId": request_id},
            anonymous=True,
        )
        return data.get("flashAuthRequestStatus") or {}

    async def prepare_artifact_upload(
        self, app_id: str, tarball_size: int
    ) -> Dict[str, Any]:
        mutation = app_mutations.MUTATION_PREPARE_ARTIFACT_UPLOAD
        data = await self._execute(
            mutation, {"input": {"flashAppId": app_id, "tarballSize": tarball_size}}
        )
        return data["prepareFlashArtifactUpload"]

    async def finalize_artifact_upload(
        self, app_id: str, object_key: str, manifest: Dict[str, Any]
    ) -> Dict[str, Any]:
        mutation = app_mutations.MUTATION_FINALIZE_ARTIFACT_UPLOAD
        data = await self._execute(
            mutation,
            {
                "input": {
                    "flashAppId": app_id,
                    "objectKey": object_key,
                    "manifest": manifest,
                }
            },
        )
        return data["finalizeFlashArtifactUpload"]

    async def deploy_build(self, environment_id: str, build_id: str) -> Dict[str, Any]:
        mutation = app_mutations.MUTATION_DEPLOY_BUILD
        data = await self._execute(
            mutation,
            {
                "input": {
                    "flashEnvironmentId": environment_id,
                    "flashBuildId": build_id,
                }
            },
        )
        return data["deployBuildToEnvironment"]

    async def upload_tarball(
        self,
        upload_url: str,
        tar_path: str,
        progress: Optional[Any] = None,
    ) -> None:
        """put the build tarball to the presigned url.

        progress, when given, is called with (bytes_sent, total_bytes)
        as chunks go out. transient resets (broken pipe, connection
        reset, 5xx) are retried with backoff; the payload is re-read
        from disk on each attempt.
        """
        import asyncio
        import os

        timeout = aiohttp.ClientTimeout(total=600)
        total = os.path.getsize(tar_path)

        async def _reader():
            sent = 0
            with open(tar_path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    sent += len(chunk)
                    if progress is not None:
                        progress(sent, total)
                    yield chunk

        attempts = 4
        for attempt in range(attempts):
            try:
                await self._put_tarball(upload_url, _reader(), total, timeout)
                return
            except (aiohttp.ClientError, OSError) as exc:
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(2**attempt)

    async def _put_tarball(self, upload_url, reader, total, timeout) -> None:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.put(
                upload_url,
                data=reader,
                headers={
                    "Content-Type": "application/gzip",
                    "Content-Length": str(total),
                },
            ) as resp:
                resp.raise_for_status()
