"""Runpod API wrapper commands."""

# pylint: disable=too-many-arguments,too-many-locals

from typing import Any, Iterable, Optional
from urllib.parse import quote

from runpod import error

from .graphql import run_graphql_query
from .mutations import container_register_auth as container_register_auth_mutations
from .queries import user as user_queries
from .rest import HTTP_STATUS_NOT_FOUND, run_rest_request


def _path_segment(value: str) -> str:
    return quote(value, safe="")


def _split_values(value: Optional[Iterable[Any] | str]) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.split(",")
    else:
        values = value
    return [str(item).strip() for item in values if str(item).strip()]


def _environment(env: Optional[dict]) -> dict[str, str]:
    return {str(key): str(value) for key, value in (env or {}).items()}


def _cpu_config(instance_id: Optional[str], min_vcpu_count: int) -> dict[str, Any]:
    if not instance_id:
        raise ValueError("instance_id must be provided for CPU pods")

    parts = instance_id.split("-")
    vcpu_count = max(2, min_vcpu_count)
    if len(parts) > 1:
        try:
            vcpu_count = int(parts[1])
        except ValueError as exc:
            raise ValueError(
                "instance_id must use the format <cpu-flavor>-<vcpu-count>-<memory>"
            ) from exc

    return {"id": parts[0], "vcpuCount": vcpu_count}


def get_user(api_key: Optional[str] = None) -> dict:
    """Get the current user."""
    raw_response = run_graphql_query(user_queries.QUERY_USER, api_key=api_key)
    return raw_response["data"]["myself"]


def update_user_settings(pubkey: str, api_key: Optional[str] = None) -> dict:
    """Replace the current user's SSH public keys."""
    keys = [key.strip() for key in pubkey.splitlines() if key.strip()]
    run_rest_request(
        "PUT",
        "/v2/account/ssh-keys",
        api_key=api_key,
        json={"keys": keys},
    )
    return get_user(api_key=api_key)


def get_gpus(api_key: Optional[str] = None) -> list[dict]:
    """Get all GPU types."""
    response = run_rest_request("GET", "/v2/catalog/gpus", api_key=api_key)
    return response["gpus"]


def get_gpu(gpu_id: str, gpu_quantity: int = 1, api_key: Optional[str] = None) -> dict:
    """Get a GPU type and its pod availability."""
    try:
        return run_rest_request(
            "GET",
            f"/v2/catalog/gpus/{_path_segment(gpu_id)}",
            api_key=api_key,
            params={
                "include": "AVAILABILITY",
                "product": "POD",
                "count": gpu_quantity,
            },
        )
    except error.QueryError as exc:
        if exc.status_code == HTTP_STATUS_NOT_FOUND:
            raise ValueError(
                "No GPU found with the specified ID, "
                "run runpod.get_gpus() to get a list of all GPUs"
            ) from exc
        raise


def get_pods(api_key: Optional[str] = None) -> list[dict]:
    """Get all standalone pods."""
    response = run_rest_request("GET", "/v2/pods", api_key=api_key)
    return response["pods"]


def get_pod(pod_id: str, api_key: Optional[str] = None) -> Optional[dict]:
    """Get a pod by ID."""
    try:
        return run_rest_request(
            "GET", f"/v2/pods/{_path_segment(pod_id)}", api_key=api_key
        )
    except error.QueryError as exc:
        if exc.status_code == HTTP_STATUS_NOT_FOUND:
            return None
        raise


def create_pod(
    name: str,
    image_name: Optional[str] = "",
    gpu_type_id: Optional[str] = None,
    cloud_type: str = "ALL",
    support_public_ip: bool = False,
    start_ssh: bool = True,
    data_center_id: Optional[str] = None,
    country_code: Optional[str] = None,
    gpu_count: int = 1,
    volume_in_gb: int = 0,
    container_disk_in_gb: Optional[int] = None,
    min_vcpu_count: int = 1,
    min_memory_in_gb: int = 1,
    docker_args: Optional[str] = None,
    ports: Optional[str] = None,
    volume_mount_path: Optional[str] = None,
    env: Optional[dict] = None,
    template_id: Optional[str] = None,
    network_volume_id: Optional[str] = None,
    allowed_cuda_versions: Optional[list] = None,
    min_download=None,
    min_upload=None,
    instance_id: Optional[str] = None,
) -> dict:
    """Create a GPU or CPU pod."""
    if not image_name and not template_id:
        raise ValueError("Either image_name or template_id must be provided")
    if cloud_type not in {"ALL", "COMMUNITY", "SECURE"}:
        raise ValueError("cloud_type must be one of ALL, COMMUNITY or SECURE")

    unsupported = []
    if support_public_ip is not False:
        unsupported.append("support_public_ip")
    if country_code is not None:
        unsupported.append("country_code")
    if not gpu_type_id and min_memory_in_gb != 1:
        unsupported.append("min_memory_in_gb")
    if not gpu_type_id and volume_mount_path is not None and not network_volume_id:
        unsupported.append("volume_mount_path on CPU pods without network_volume_id")
    if min_download is not None:
        unsupported.append("min_download")
    if min_upload is not None:
        unsupported.append("min_upload")
    if unsupported:
        fields = ", ".join(unsupported)
        raise ValueError(f"REST API v2 does not support: {fields}")

    body: dict[str, Any] = {
        "name": name,
        "startSsh": start_ssh,
    }
    if docker_args is not None:
        body["args"] = docker_args
    if image_name:
        body["image"] = image_name
    if template_id:
        body["templateId"] = template_id
    if cloud_type != "ALL":
        body["cloud"] = cloud_type
    if data_center_id:
        body["dataCenterIds"] = [data_center_id]
    if container_disk_in_gb is not None:
        body["disk"] = container_disk_in_gb
    elif not template_id:
        body["disk"] = 10
    if ports is not None:
        body["ports"] = _split_values(ports)
    if env is not None:
        body["env"] = _environment(env)

    mount_path = (
        volume_mount_path if volume_mount_path is not None else "/runpod-volume"
    )
    if network_volume_id:
        body["mounts"] = {
            "network": [{"volumeId": network_volume_id, "path": mount_path}]
        }
    elif volume_in_gb:
        body["mounts"] = {"persistent": {"size": volume_in_gb, "path": mount_path}}
    elif template_id and gpu_type_id and volume_mount_path is not None:
        template = run_rest_request(
            "GET", f"/v2/templates/{_path_segment(template_id)}"
        )
        persistent = template.get("mounts", {}).get("persistent")
        if persistent is not None:
            body["mounts"] = {
                "persistent": {"size": persistent["size"], "path": volume_mount_path}
            }

    if gpu_type_id:
        gpu: dict[str, Any] = {"id": gpu_type_id, "count": gpu_count}
        if min_memory_in_gb != 1:
            gpu["minRamPerGpu"] = min_memory_in_gb
        if min_vcpu_count != 1:
            gpu["minVcpuCountPerGpu"] = min_vcpu_count
        if allowed_cuda_versions is not None:
            gpu["allowedCudaVersions"] = _split_values(allowed_cuda_versions)
        body["gpu"] = gpu
    else:
        body["cpu"] = _cpu_config(instance_id, min_vcpu_count)

    return run_rest_request("POST", "/v2/pods", json=body)


def stop_pod(pod_id: str) -> dict:
    """Stop a pod."""
    return run_rest_request(
        "POST",
        f"/v2/pods/{_path_segment(pod_id)}/action",
        json={"action": "stop"},
    )


def resume_pod(pod_id: str, gpu_count: int) -> dict:
    """Start a stopped pod."""
    _ = gpu_count
    return run_rest_request(
        "POST",
        f"/v2/pods/{_path_segment(pod_id)}/action",
        json={"action": "start"},
    )


def terminate_pod(pod_id: str) -> None:
    """Terminate a pod."""
    run_rest_request("DELETE", f"/v2/pods/{_path_segment(pod_id)}")


def create_template(
    name: str,
    image_name: str,
    docker_start_cmd: str = None,
    container_disk_in_gb: int = 10,
    volume_in_gb: int = None,
    volume_mount_path: str = None,
    ports: str = None,
    env: dict = None,
    is_serverless: bool = False,
    registry_auth_id: str = None,
) -> dict:
    """Create a pod or serverless template."""
    body: dict[str, Any] = {
        "name": name,
        "image": image_name,
        "disk": container_disk_in_gb,
        "serverless": is_serverless,
    }
    if docker_start_cmd is not None:
        body["args"] = docker_start_cmd
    if volume_in_gb:
        body["mounts"] = {
            "persistent": {
                "size": volume_in_gb,
                "path": volume_mount_path or "/workspace",
            }
        }
    if ports is not None:
        body["ports"] = _split_values(ports)
    if env is not None:
        body["env"] = _environment(env)
    if registry_auth_id is not None:
        body["registry"] = registry_auth_id

    return run_rest_request("POST", "/v2/templates", json=body)


def get_endpoints() -> list[dict]:
    """Get all serverless endpoints."""
    response = run_rest_request("GET", "/v2/serverless")
    return response["endpoints"]


def create_endpoint(
    name: str,
    template_id: str,
    gpu_ids: str = "AMPERE_16",
    network_volume_id: str = None,
    locations: str = None,
    idle_timeout: Optional[int] = None,
    scaler_type: str = "QUEUE_DELAY",
    scaler_value: int = 4,
    workers_min: int = 0,
    workers_max: int = 3,
    flashboot=False,
    allowed_cuda_versions: str = None,
    gpu_count: int = 1,
) -> dict:
    """Create a queue-based serverless endpoint; locations are datacenter IDs."""
    if scaler_type == "QUEUE_DELAY":
        scaling = {"type": scaler_type, "queueDelay": scaler_value}
    elif scaler_type == "REQUEST_COUNT":
        if idle_timeout is not None:
            raise ValueError("idle_timeout is only supported with QUEUE_DELAY scaling")
        scaling = {"type": scaler_type, "requestCount": scaler_value}
    else:
        raise ValueError("scaler_type must be QUEUE_DELAY or REQUEST_COUNT")

    pools = []
    excluded_types = []
    for gpu_id in _split_values(gpu_ids):
        if gpu_id.startswith("-"):
            excluded_type = gpu_id[1:].strip()
            if excluded_type and excluded_type not in excluded_types:
                excluded_types.append(excluded_type)
        else:
            pools.append(gpu_id)

    gpu: dict[str, Any] = {
        "pools": pools,
        "count": gpu_count,
    }
    if excluded_types:
        gpu["excludedTypes"] = excluded_types
    if allowed_cuda_versions is not None:
        gpu["allowedCudaVersions"] = _split_values(allowed_cuda_versions)

    workers = {"min": workers_min, "max": workers_max}
    if scaler_type == "QUEUE_DELAY":
        workers["idleTimeout"] = 5 if idle_timeout is None else idle_timeout

    body: dict[str, Any] = {
        "name": name,
        "templateId": template_id,
        "type": "QUEUE",
        "gpu": gpu,
        "workers": workers,
        "scaling": scaling,
        "flashboot": "FLASHBOOT" if flashboot else "OFF",
    }
    if network_volume_id:
        body["networkVolumes"] = [network_volume_id]
    if locations:
        body["dataCenterIds"] = _split_values(locations)

    return run_rest_request("POST", "/v2/serverless", json=body)


def update_endpoint_template(endpoint_id: str, template_id: str) -> dict:
    """Apply a serverless template to an endpoint."""
    return run_rest_request(
        "PATCH",
        f"/v2/serverless/{_path_segment(endpoint_id)}",
        json={"templateId": template_id},
    )


def create_container_registry_auth(name: str, username: str, password: str) -> dict:
    """Create a container registry credential."""
    return run_rest_request(
        "POST",
        "/v2/registries",
        json={"name": name, "username": username, "password": password},
    )


def update_container_registry_auth(
    registry_auth_id: str, username: str, password: str
) -> dict:
    """Update a container registry credential."""
    raw_response = run_graphql_query(
        container_register_auth_mutations.update_container_registry_auth(
            registry_auth_id, username, password
        )
    )
    return raw_response["data"]["updateRegistryAuth"]


def delete_container_registry_auth(registry_auth_id: str) -> bool:
    """Delete a container registry credential."""
    run_rest_request("DELETE", f"/v2/registries/{_path_segment(registry_auth_id)}")
    return True
