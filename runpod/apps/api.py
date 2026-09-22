"""control-plane calls for app provisioning.

rest handles equivalent resource operations; graphql handles flash lifecycle,
auth, secrets, task provisioning, and endpoint capabilities absent from rest.
management verbs for the wider sdk stay in runpod.api.ctl_commands.
"""

from typing import Any, Dict, List, Optional

import aiohttp

from ..api.ctl_commands import _cpu_config, _path_segment, _split_values
from ..api.graphql import run_graphql_query_async
from ..api.mutations import apps as app_mutations
from ..api.queries import apps as app_queries
from ..api.rest import run_rest_request_async
from ..error import QueryError

_TRANSPORT_RETRIES = 4


def _rest_endpoint_input(endpoint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """translate only saves whose capabilities rest can preserve atomically."""
    fields = {
        "id",
        "name",
        "type",
        "template",
        "templateId",
        "workersMin",
        "workersMax",
        "idleTimeout",
        "scalerType",
        "scalerValue",
        "executionTimeoutMs",
        "flashBootType",
        "locations",
        "instanceIds",
        "gpuIds",
        "gpuCount",
        "minCudaVersion",
        "allowedCudaVersions",
        "networkVolumeIds",
    }
    template_fields = {
        "imageName": "image",
        "containerDiskInGb": "disk",
        "dockerArgs": "args",
        "containerRegistryAuthId": "registry",
    }
    template = endpoint.get("template") or {}
    # flash bindings, cached models, schedules, and template metadata need gql.
    if endpoint.keys() - fields or template.keys() - (
        template_fields.keys() | {"env", "ports"}
    ):
        return None
    if endpoint.get("type", "QB") not in {"QB", "LB"}:
        return None
    scaler = endpoint.get("scalerType")
    if scaler not in {None, "QUEUE_DELAY", "REQUEST_COUNT"}:
        return None
    if ("scalerType" in endpoint) != ("scalerValue" in endpoint):
        return None
    if not endpoint.get("id") and (
        scaler is None or not (template.get("imageName") or endpoint.get("templateId"))
    ):
        return None

    body = {
        rest: template[gql] for gql, rest in template_fields.items() if gql in template
    }
    if "env" in template:
        body["env"] = {item["key"]: item["value"] for item in template["env"]}
    if "ports" in template:
        body["ports"] = _split_values(template["ports"])
    for gql, rest in (
        ("name", "name"),
        ("templateId", "templateId"),
        ("executionTimeoutMs", "timeout"),
        ("flashBootType", "flashboot"),
    ):
        if gql in endpoint:
            body[rest] = endpoint[gql]
    if "networkVolumeIds" in endpoint:
        volumes = endpoint["networkVolumeIds"]
        if any(set(volume) != {"networkVolumeId"} for volume in volumes):
            return None
        body["networkVolumes"] = [volume["networkVolumeId"] for volume in volumes]
    if not endpoint.get("id"):
        body["type"] = "LOAD_BALANCER" if endpoint.get("type") == "LB" else "QUEUE"
    if "locations" in endpoint:
        body["dataCenterIds"] = _split_values(endpoint["locations"])
    if "instanceIds" in endpoint:
        body["cpu"] = [_cpu_config(instance) for instance in endpoint["instanceIds"]]
        # one-vcpu endpoints require graphql.
        if any(cpu["vcpuCount"] < 2 for cpu in body["cpu"]):
            return None
    gpu = {}
    if "gpuIds" in endpoint:
        tokens = _split_values(endpoint["gpuIds"])
        gpu["pools"] = [token for token in tokens if not token.startswith("-")]
        gpu["excludedTypes"] = [token[1:] for token in tokens if token.startswith("-")]
    for key in ("gpuCount", "minCudaVersion", "allowedCudaVersions"):
        if key in endpoint:
            gpu["count" if key == "gpuCount" else key] = (
                _split_values(endpoint[key])
                if key == "allowedCudaVersions"
                else endpoint[key]
            )
    if gpu:
        body["gpu"] = gpu
    workers = {
        rest: endpoint[gql]
        for gql, rest in (
            ("workersMin", "min"),
            ("workersMax", "max"),
            ("idleTimeout", "idleTimeout"),
        )
        if gql in endpoint
    }
    # queue request-count scaling ignores idle timeout upstream and rest rejects it.
    if (
        not endpoint.get("id")
        and endpoint.get("type", "QB") == "QB"
        and scaler == "REQUEST_COUNT"
    ):
        workers.pop("idleTimeout", None)
    if workers:
        body["workers"] = workers
    if scaler is not None:
        key = "queueDelay" if scaler == "QUEUE_DELAY" else "requestCount"
        body["scaling"] = {"type": scaler, key: endpoint["scalerValue"]}
    return body


def _stock_in_datacenter(data: Dict[str, Any], data_center_id: str) -> str:
    """catalog omits datacenters where the requested configuration is unavailable."""
    return next(
        (
            dc["availability"]
            for dc in data.get("dataCenters", [])
            if dc["id"] == data_center_id
        ),
        "NONE",
    )


def is_capacity_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        phrase in message
        for phrase in (
            "resources to deploy your pod",
            "insufficient capacity",
            "no available machine",
            "out of stock",
        )
    )


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
        retry: bool = False,
    ) -> Dict[str, Any]:
        import asyncio

        attempts = _TRANSPORT_RETRIES if retry else 1
        for attempt in range(attempts - 1):
            try:
                response = await run_graphql_query_async(
                    query,
                    api_key=self._api_key,
                    variables=variables,
                    anonymous=anonymous,
                )
                return response["data"]
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError):
                await asyncio.sleep(2**attempt)
        response = await run_graphql_query_async(
            query,
            api_key=self._api_key,
            variables=variables,
            anonymous=anonymous,
        )
        return response["data"]

    async def save_endpoint(self, endpoint_input: Dict[str, Any]) -> Dict[str, Any]:
        """save via rest unless the input requires an unsupported capability."""
        body = _rest_endpoint_input(endpoint_input)
        endpoint_id = endpoint_input.get("id")
        if body is not None and endpoint_id:
            current = await run_rest_request_async(
                "GET",
                f"/v2/serverless/{_path_segment(endpoint_id)}",
                api_key=self._api_key,
            )
            if not current:
                raise QueryError(
                    f"endpoint '{endpoint_id}' returned an empty response",
                    f"GET /v2/serverless/{_path_segment(endpoint_id)}",
                )
            requested_type = endpoint_input.get("type")
            expected = (
                ("LOAD_BALANCER" if requested_type == "LB" else "QUEUE")
                if requested_type is not None
                else current.get("type")
            )
            # rest updates cannot change routing type or compute family.
            if (
                current.get("type") != expected
                or ("cpu" in body and not current.get("cpu"))
                or ("gpu" in body and current.get("cpu"))
            ):
                body = None
            elif (
                current.get("type") == "QUEUE"
                and body.get("scaling", current.get("scaling", {})).get("type")
                == "REQUEST_COUNT"
            ):
                body.get("workers", {}).pop("idleTimeout", None)
        if body is not None:
            path = "/v2/serverless"
            if endpoint_id:
                path += f"/{_path_segment(endpoint_id)}"
            return await run_rest_request_async(
                "PATCH" if endpoint_id else "POST",
                path,
                api_key=self._api_key,
                json=body,
            )
        template = endpoint_input.get("template")
        if template is not None and "name" not in template and "name" in endpoint_input:
            # graphql requires a template name; rest names the bound template itself.
            endpoint_input = {
                **endpoint_input,
                "template": {"name": endpoint_input["name"], **template},
            }
        mutation = app_mutations.MUTATION_SAVE_ENDPOINT
        data = await self._execute(mutation, {"input": endpoint_input})
        return data["saveEndpoint"]

    async def delete_endpoint(self, endpoint_id: str) -> bool:
        await run_rest_request_async(
            "DELETE",
            f"/v2/serverless/{_path_segment(endpoint_id)}",
            api_key=self._api_key,
        )
        return True

    async def list_my_endpoints(self) -> List[Dict[str, Any]]:
        data = await run_rest_request_async(
            "GET", "/v2/serverless", api_key=self._api_key
        )
        return data["endpoints"]

    async def endpoint_workers(self, endpoint_id: str) -> Dict[str, Any]:
        """worker state uses the user key."""
        return await run_rest_request_async(
            "GET",
            f"/v2/serverless/{_path_segment(endpoint_id)}/workers",
            api_key=self._api_key,
            timeout=10,
        )

    async def deploy_task_pod(
        self, pod_input: Dict[str, Any], *, is_cpu: bool
    ) -> Dict[str, Any]:
        """provision with a termination deadline and public-ip requirement."""
        pod_input = dict(pod_input)
        if is_cpu:
            instance_ids = pod_input.pop("instanceIds", None)
            if instance_ids is None:
                instance_ids = [pod_input.pop("instanceId", None)]
            if (
                not isinstance(instance_ids, list)
                or not instance_ids
                or any(
                    not isinstance(instance_id, str) or not instance_id.strip()
                    for instance_id in instance_ids
                )
            ):
                raise ValueError("cpu tasks require non-empty instance ids")
            mutation = app_mutations.MUTATION_DEPLOY_CPU_POD
            for index, instance_id in enumerate(instance_ids):
                candidate = dict(pod_input, instanceId=instance_id)
                try:
                    data = await self._execute(mutation, {"input": candidate})
                    return data["deployCpuPod"]
                except QueryError as exc:
                    if not is_capacity_error(exc) or index == len(instance_ids) - 1:
                        raise

        mutation = app_mutations.MUTATION_DEPLOY_POD
        data = await self._execute(mutation, {"input": pod_input})
        return data["podFindAndDeployOnDemand"]

    async def terminate_pod(self, pod_id: str) -> None:
        await run_rest_request_async(
            "DELETE", f"/v2/pods/{_path_segment(pod_id)}", api_key=self._api_key
        )

    async def get_app_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        query = app_queries.QUERY_FLASH_APP_BY_NAME
        try:
            data = await self._execute(query, {"flashAppName": name}, retry=True)
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
        """secure-cloud stock scoped to GPU count and pod/serverless product."""
        data = await run_rest_request_async(
            "GET",
            f"/v2/catalog/gpus/{_path_segment(gpu_id)}",
            api_key=self._api_key,
            params={
                "include": "AVAILABILITY",
                "product": "POD" if pods else "SERVERLESS",
                "count": gpu_count,
                "cloud": "SECURE",
            },
        )
        return _stock_in_datacenter(data, data_center_id)

    async def cpu_stock_status(
        self, instance_id: str, data_center_id: str, *, pods: bool = False
    ) -> Optional[str]:
        """stock scoped to CPU flavor, vCPU count, and pod/serverless product."""
        cpu = _cpu_config(instance_id)
        # rest stock filters require a power-of-two count of at least two.
        count = cpu["vcpuCount"]
        if count < 2 or count & (count - 1):
            data = await self._execute(
                app_queries.QUERY_CPU_STOCK,
                {
                    "cpuFlavorInput": {"id": cpu["id"], "isSls": not pods},
                    "specificsInput": {
                        "dataCenterId": data_center_id,
                        "instanceId": instance_id,
                        "isSls": not pods,
                    },
                },
                retry=True,
            )
            flavors = data.get("cpuFlavors") or []
            specifics = (flavors[0].get("specifics") or {}) if flavors else {}
            return specifics.get("stockStatus")
        data = await run_rest_request_async(
            "GET",
            f"/v2/catalog/cpus/{_path_segment(cpu['id'])}",
            api_key=self._api_key,
            params={
                "include": "AVAILABILITY",
                "product": "POD" if pods else "SERVERLESS",
                "vcpuCount": cpu["vcpuCount"],
            },
        )
        return _stock_in_datacenter(data, data_center_id)

    async def list_network_volumes(self) -> List[Dict[str, Any]]:
        data = await run_rest_request_async(
            "GET", "/v2/network-volumes", api_key=self._api_key
        )
        return data["networkVolumes"]

    async def create_network_volume(
        self, name: str, size: int, data_center_id: str
    ) -> Dict[str, Any]:
        return await run_rest_request_async(
            "POST",
            "/v2/network-volumes",
            api_key=self._api_key,
            json={"name": name, "size": size, "dataCenter": data_center_id},
        )

    async def list_registry_auths(self) -> List[Dict[str, Any]]:
        data = await run_rest_request_async(
            "GET", "/v2/registries", api_key=self._api_key
        )
        return data["registries"]

    async def create_registry_auth(
        self, name: str, username: str, password: str
    ) -> Dict[str, Any]:
        return await run_rest_request_async(
            "POST",
            "/v2/registries",
            api_key=self._api_key,
            json={"name": name, "username": username, "password": password},
        )

    async def delete_registry_auth(self, auth_id: str) -> bool:
        await run_rest_request_async(
            "DELETE",
            f"/v2/registries/{_path_segment(auth_id)}",
            api_key=self._api_key,
        )
        return True

    async def list_secrets(self) -> List[Dict[str, Any]]:
        query = app_queries.QUERY_SECRETS
        data = await self._execute(query, retry=True)
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
        data = await self._execute(query, retry=True)
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
                retry=True,
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
            retry=True,
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
            except (aiohttp.ClientError, OSError):
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
