"""control-plane calls for app provisioning.

a thin wrapper over runpod.api.graphql's shared async transport,
scoped to what the apps surface needs: endpoint save/delete for dev
sessions, and the app / build / environment lifecycle for deploys.
management verbs for the wider sdk stay in runpod.api.ctl_commands.
"""

from typing import Any, Dict, List, Optional

import aiohttp

from ..api.graphql import run_graphql_query_async
from ..error import QueryError


class AppsApiClient:
    """async control-plane client scoped to apps provisioning."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key

    async def _execute(
        self, query: str, variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        response = await run_graphql_query_async(
            query, api_key=self._api_key, variables=variables
        )
        return response["data"]

    # -- endpoints (dev session provisioning) --

    async def save_endpoint(self, endpoint_input: Dict[str, Any]) -> Dict[str, Any]:
        """create or update a serverless endpoint. include id to update."""
        mutation = """
        mutation saveEndpoint($input: EndpointInput!) {
            saveEndpoint(input: $input) {
                id
                name
                templateId
                gpuIds
                instanceIds
                workersMin
                workersMax
                idleTimeout
            }
        }
        """
        data = await self._execute(mutation, {"input": endpoint_input})
        return data["saveEndpoint"]

    async def delete_endpoint(self, endpoint_id: str) -> bool:
        mutation = """
        mutation deleteEndpoint($id: String!) {
            deleteEndpoint(id: $id)
        }
        """
        data = await self._execute(mutation, {"id": endpoint_id})
        return "deleteEndpoint" in data

    async def list_my_endpoints(self) -> List[Dict[str, Any]]:
        query = """
        query myEndpoints {
            myself {
                endpoints { id name templateId workersMin workersMax }
            }
        }
        """
        data = await self._execute(query)
        return data["myself"]["endpoints"]

    # -- app lifecycle (deploy) --

    async def get_app_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        query = """
        query getFlashAppByName($flashAppName: String!) {
            flashAppByName(flashAppName: $flashAppName) {
                id
                name
                flashEnvironments { id name state activeBuildId }
            }
        }
        """
        try:
            data = await self._execute(query, {"flashAppName": name})
        except QueryError as exc:
            if "not found" in str(exc).lower():
                return None
            raise
        return data["flashAppByName"]

    async def create_app(self, name: str) -> Dict[str, Any]:
        mutation = """
        mutation createFlashApp($input: CreateFlashAppInput!) {
            createFlashApp(input: $input) { id name }
        }
        """
        data = await self._execute(mutation, {"input": {"name": name}})
        return data["createFlashApp"]

    async def create_environment(self, app_id: str, name: str) -> Dict[str, Any]:
        mutation = """
        mutation createFlashEnvironment($input: CreateFlashEnvironmentInput!) {
            createFlashEnvironment(input: $input) { id name }
        }
        """
        data = await self._execute(
            mutation, {"input": {"flashAppId": app_id, "name": name}}
        )
        return data["createFlashEnvironment"]

    async def prepare_artifact_upload(
        self, app_id: str, tarball_size: int
    ) -> Dict[str, Any]:
        mutation = """
        mutation PrepareArtifactUpload($input: PrepareFlashArtifactUploadInput!) {
            prepareFlashArtifactUpload(input: $input) {
                uploadUrl
                objectKey
                expiresAt
            }
        }
        """
        data = await self._execute(
            mutation, {"input": {"flashAppId": app_id, "tarballSize": tarball_size}}
        )
        return data["prepareFlashArtifactUpload"]

    async def finalize_artifact_upload(
        self, app_id: str, object_key: str, manifest: Dict[str, Any]
    ) -> Dict[str, Any]:
        mutation = """
        mutation FinalizeArtifactUpload($input: FinalizeFlashArtifactUploadInput!) {
            finalizeFlashArtifactUpload(input: $input) { id manifest }
        }
        """
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

    async def deploy_build(
        self, environment_id: str, build_id: str
    ) -> Dict[str, Any]:
        mutation = """
        mutation deployBuildToEnvironment($input: DeployBuildToEnvironmentInput!) {
            deployBuildToEnvironment(input: $input) { id name }
        }
        """
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

    async def upload_tarball(self, upload_url: str, tar_path: str) -> None:
        """put the build tarball to the presigned url."""
        timeout = aiohttp.ClientTimeout(total=600)
        with open(tar_path, "rb") as f:
            body = f.read()
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.put(
                upload_url,
                data=body,
                headers={"Content-Type": "application/gzip"},
            ) as resp:
                resp.raise_for_status()
