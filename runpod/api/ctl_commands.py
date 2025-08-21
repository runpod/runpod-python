"""
Runpod | API Wrapper | CTL Commands
"""

# pylint: disable=too-many-arguments,too-many-locals

from typing import Optional

from .graphql import run_graphql_query
from .mutations import container_register_auth as container_register_auth_mutations
from .mutations import endpoints as endpoint_mutations
from .mutations import pods as pod_mutations
from .mutations import templates as template_mutations
from .mutations import user as user_mutations
from .queries import endpoints as endpoint_queries
from .queries import gpus
from .queries import pods as pod_queries
from .queries import user as user_queries


def get_user(api_key: Optional[str] = None) -> dict:
    """
    Get the current user with optional API key override.
    
    Args:
        api_key: Optional API key to use for this query.
    """
    raw_response = run_graphql_query(user_queries.QUERY_USER, api_key=api_key)
    cleaned_return = raw_response["data"]["myself"]
    return cleaned_return


def update_user_settings(pubkey: str, api_key: Optional[str] = None) -> dict:
    """
    Update the current user

    Args:
        pubkey: the public key of the user
        api_key: Optional API key to use for this query.
    """
    raw_response = run_graphql_query(
        user_mutations.generate_user_mutation(pubkey), 
        api_key=api_key
    )
    cleaned_return = raw_response["data"]["updateUserSettings"]
    return cleaned_return


def get_gpus(api_key: Optional[str] = None) -> dict:
    """
    Get all GPU types
    
    Args:
        api_key: Optional API key to use for this query.
    """
    raw_response = run_graphql_query(gpus.QUERY_GPU_TYPES, api_key=api_key)
    cleaned_return = raw_response["data"]["gpuTypes"]
    return cleaned_return


def get_gpu(gpu_id: str, gpu_quantity: int = 1, api_key: Optional[str] = None):
    """
    Get a specific GPU type

    Args:
        gpu_id: the id of the gpu
        gpu_quantity: how many of the gpu should be returned
        api_key: Optional API key to use for this query.
    """
    raw_response = run_graphql_query(
        gpus.generate_gpu_query(gpu_id, gpu_quantity), 
        api_key=api_key
    )

    cleaned_return = raw_response["data"]["gpuTypes"]

    if len(cleaned_return) < 1:
        raise ValueError(
            "No GPU found with the specified ID, "
            "run runpod.get_gpus() to get a list of all GPUs"
        )

    return cleaned_return[0]


def get_pods(api_key: Optional[str] = None) -> dict:
    """
    Get all pods
    
    Args:
        api_key: Optional API key to use for this query.
    """
    raw_return = run_graphql_query(pod_queries.QUERY_POD, api_key=api_key)
    cleaned_return = raw_return["data"]["myself"]["pods"]
    return cleaned_return


def get_pod(pod_id: str, api_key: Optional[str] = None):
    """
    Get a specific pod

    Args:
        pod_id: the id of the pod
        api_key: Optional API key to use for this query.
    """
    raw_response = run_graphql_query(
        pod_queries.generate_pod_query(pod_id), 
        api_key=api_key
    )
    return raw_response["data"]["pod"]


def create_pod(
    name: str,
    image_name: Optional[str] = "",
    gpu_type_id: Optional[str] = None,
    cloud_type: str = "ALL",
    support_public_ip: bool = True,
    start_ssh: bool = True,
    data_center_id: Optional[str] = None,
    country_code: Optional[str] = None,
    gpu_count: int = 1,
    volume_in_gb: int = 0,
    container_disk_in_gb: Optional[int] = None,
    min_vcpu_count: int = 1,
    min_memory_in_gb: int = 1,
    docker_args: str = "",
    ports: Optional[str] = None,
    volume_mount_path: str = "/runpod-volume",
    env: Optional[dict] = None,
    template_id: Optional[str] = None,
    network_volume_id: Optional[str] = None,
    allowed_cuda_versions: Optional[list] = None,
    min_download = None,
    min_upload = None,
    instance_id: Optional[str] = None,
) -> dict:
    """
    Create a pod

    :param name: the name of the pod
    :param image_name: the name of the docker image to be used by the pod
    :param gpu_type_id: the gpu type wanted by the pod (retrievable by get_gpus). If None, creates a CPU-only pod
    :param cloud_type: if secure cloud, community cloud or all is wanted
    :param data_center_id: the id of the data center
    :param country_code: the code for country to start the pod in
    :param gpu_count: how many gpus should be attached to the pod (ignored for CPU-only pods)
    :param volume_in_gb: how big should the pod volume be
    :param ports: the ports to open in the pod, example format - "8888/http,666/tcp"
    :param volume_mount_path: where to mount the volume?
    :param env: the environment variables to inject into the pod,
                for example {EXAMPLE_VAR:"example_value", EXAMPLE_VAR2:"example_value 2"}, will
                inject EXAMPLE_VAR and EXAMPLE_VAR2 into the pod with the mentioned values
    :param template_id: the id of the template to use for the pod
    :param min_download: minimum download speed in Mbps
    :param min_upload: minimum upload speed in Mbps
    :param instance_id: the id of a specific instance to deploy to (for CPU pods)
    :example:

    >>> # Create GPU pod
    >>> pod_id = runpod.create_pod("test", "runpod/stack", "NVIDIA GeForce RTX 3070")
    >>> # Create CPU pod
    >>> pod_id = runpod.create_pod("test", "runpod/stack")
    >>> # Create CPU pod on specific instance
    >>> pod_id = runpod.create_pod("test", "runpod/stack", instance_id="cpu3c-2-4")
    """
    # Input Validation

    if not image_name and not template_id:
        raise ValueError("Either image_name or template_id must be provided")

    if gpu_type_id is not None:
        get_gpu(gpu_type_id)  # Check if GPU exists, will raise ValueError if not.
    if cloud_type not in ["ALL", "COMMUNITY", "SECURE"]:
        raise ValueError("cloud_type must be one of ALL, COMMUNITY or SECURE")

    if network_volume_id and data_center_id is None:
        user_info = get_user()
        for network_volume in user_info["networkVolumes"]:
            if network_volume["id"] == network_volume_id:
                data_center_id = network_volume["dataCenterId"]
                break

    if container_disk_in_gb is None and template_id is None:
        container_disk_in_gb = 10

    raw_response = run_graphql_query(
        pod_mutations.generate_pod_deployment_mutation(
            name,
            image_name,
            gpu_type_id,
            cloud_type,
            support_public_ip,
            start_ssh,
            data_center_id,
            country_code,
            gpu_count if gpu_type_id is not None else None,
            volume_in_gb,
            container_disk_in_gb,
            min_vcpu_count,
            min_memory_in_gb,
            docker_args,
            ports,
            volume_mount_path,
            env,
            template_id,
            network_volume_id,
            allowed_cuda_versions,
            min_download,
            min_upload,
            instance_id,
        )
    )

    print(f"raw_response: {raw_response}")

    if gpu_type_id is not None:
        cleaned_response = raw_response["data"]["podFindAndDeployOnDemand"]
    else:
        cleaned_response = raw_response["data"]["deployCpuPod"]
    return cleaned_response


def stop_pod(pod_id: str):
    """
    Stop a pod

    :param pod_id: the id of the pod

    :example:

    >>> pod_id = runpod.create_pod("test", "runpod/stack", "NVIDIA GeForce RTX 3070")
    >>> runpod.stop_pod(pod_id)
    """
    raw_response = run_graphql_query(pod_mutations.generate_pod_stop_mutation(pod_id))

    cleaned_response = raw_response["data"]["podStop"]
    return cleaned_response


def resume_pod(pod_id: str, gpu_count: int):
    """
    Resume a pod

    :param pod_id: the id of the pod
    :param gpu_count: the number of GPUs to attach to the pod

    :example:

    >>> pod_id = runpod.create_pod("test", "runpod/stack", "NVIDIA GeForce RTX 3070")
    >>> runpod.stop_pod(pod_id)
    >>> runpod.resume_pod(pod_id)
    """
    raw_response = run_graphql_query(
        pod_mutations.generate_pod_resume_mutation(pod_id, gpu_count)
    )

    cleaned_response = raw_response["data"]["podResume"]
    return cleaned_response


def terminate_pod(pod_id: str):
    """
    Terminate a pod

    :param pod_id: the id of the pod

    :example:

    >>> pod_id = runpod.create_pod("test", "runpod/stack", "NVIDIA GeForce RTX 3070")
    >>> runpod.terminate_pod(pod_id)
    """
    run_graphql_query(pod_mutations.generate_pod_terminate_mutation(pod_id))


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
):
    """
    Create a template

    :param name: the name of the template
    :param image_name: the name of the docker image to be used by the template
    :param docker_start_cmd: the command to start the docker container with
    :param container_disk_in_gb: how big should the container disk be
    :param volume_in_gb: how big should the volume be
    :param ports: the ports to open in the pod, example format - "8888/http,666/tcp"
    :param volume_mount_path: where to mount the volume?
    :param env: the environment variables to inject into the pod,
                for example {EXAMPLE_VAR:"example_value", EXAMPLE_VAR2:"example_value 2"}, will
                inject EXAMPLE_VAR and EXAMPLE_VAR2 into the pod with the mentioned values
    :param is_serverless: is the template serverless?
    :param registry_auth_id: the id of the registry auth

    :example:

    >>> template_id = runpod.create_template("test", "runpod/stack", "python3 main.py")
    """
    raw_response = run_graphql_query(
        template_mutations.generate_pod_template(
            name=name,
            image_name=image_name,
            docker_start_cmd=docker_start_cmd,
            container_disk_in_gb=container_disk_in_gb,
            volume_in_gb=volume_in_gb,
            volume_mount_path=volume_mount_path,
            ports=ports,
            env=env,
            is_serverless=is_serverless,
            registry_auth_id=registry_auth_id,
        )
    )

    return raw_response["data"]["saveTemplate"]


def get_endpoints() -> dict:
    """
    Get all endpoints
    """
    raw_return = run_graphql_query(endpoint_queries.QUERY_ENDPOINT)
    cleaned_return = raw_return["data"]["myself"]["endpoints"]
    return cleaned_return


def create_endpoint(
    name: str,
    template_id: str,
    gpu_ids: str = "AMPERE_16",
    network_volume_id: str = None,
    locations: str = None,
    idle_timeout: int = 5,
    scaler_type: str = "QUEUE_DELAY",
    scaler_value: int = 4,
    workers_min: int = 0,
    workers_max: int = 3,
    flashboot=False,
    allowed_cuda_versions: str = None,
    gpu_count: int = 1,
):
    """
    Create an endpoint

    :param name: the name of the endpoint
    :param template_id: the id of the template to use for the endpoint
    :param gpu_ids: the ids of the GPUs to use for the endpoint
    :param network_volume_id: the id of the network volume to use for the endpoint
    :param locations: the locations to use for the endpoint
    :param idle_timeout: the idle timeout for the endpoint
    :param scaler_type: the scaler type for the endpoint
    :param scaler_value: the scaler value for the endpoint
    :param workers_min: the minimum number of workers for the endpoint
    :param workers_max: the maximum number of workers for the endpoint
    :param allowed_cuda_versions: Comma-separated list of allowed CUDA versions (e.g., ["12.4", "12.5"]).
    :param gpu_count: the number of GPUs to use for the endpoint

    :example:

    >>> endpoint_id = runpod.create_endpoint("test", "template_id")
    """
    raw_response = run_graphql_query(
        endpoint_mutations.generate_endpoint_mutation(
            name,
            template_id,
            gpu_ids,
            network_volume_id,
            locations,
            idle_timeout,
            scaler_type,
            scaler_value,
            workers_min,
            workers_max,
            flashboot,
            allowed_cuda_versions,
            gpu_count
        )
    )

    return raw_response["data"]["saveEndpoint"]


def update_endpoint_template(endpoint_id: str, template_id: str):
    """
    Update an endpoint template

    :param endpoint_id: the id of the endpoint
    :param template_id: the id of the template to use for the endpoint

    :example:

    >>> endpoint_id = runpod.update_endpoint_template("test", "template_id")
    """
    raw_response = run_graphql_query(
        endpoint_mutations.update_endpoint_template_mutation(endpoint_id, template_id)
    )

    return raw_response["data"]["updateEndpointTemplate"]


def create_container_registry_auth(name: str, username: str, password: str):
    """
    Create a container registry authentication.

    Args:
        name (str): The name of the container registry.
        username (str): The username for authentication.
        password (str): The password for authentication.

    Returns:
        dict: The response data containing the saved container registry authentication.
    """
    raw_response = run_graphql_query(
        container_register_auth_mutations.generate_container_registry_auth(
            name, username, password
        )
    )
    return raw_response["data"]["saveRegistryAuth"]


def update_container_registry_auth(registry_auth_id: str, username: str, password: str):
    """
    Update a container registry authentication.

    Args:
        registry_auth_id (str): The id of the container registry authentication
        username (str): The username for authentication.
        password (str): The password for authentication.

    Returns:
        dict: The response data containing the updated container registry authentication.
    """
    raw_response = run_graphql_query(
        container_register_auth_mutations.update_container_registry_auth(
            registry_auth_id, username, password
        )
    )
    return raw_response["data"]["updateRegistryAuth"]


def delete_container_registry_auth(registry_auth_id: str):
    """
    Delete a container registry authentication.

    Args:
        registry_auth_id (str): The id of the container registry authentication
    """
    raw_response = run_graphql_query(
        container_register_auth_mutations.delete_container_registry_auth(
            registry_auth_id
        )
    )
    return raw_response["data"]["deleteRegistryAuth"]
