"""
RunPod | API Wrapper | CTL Commands
"""
# pylint: disable=too-many-arguments,too-many-locals

from typing import Optional

from .queries import user as user_queries
from .mutations import user as user_mutations
from .queries import gpus
from .queries import pods as pod_queries
from .graphql import run_graphql_query
from .mutations import pods as pod_mutations

def get_user() -> dict:
    '''
    Get the current user
    '''
    raw_response = run_graphql_query(user_queries.QUERY_USER)
    cleaned_return = raw_response["data"]["myself"]
    return cleaned_return

def update_user_settings(pubkey : str) -> dict:
    '''
    Update the current user
    :param pubkey: the public key of the user
    '''
    raw_response = run_graphql_query(user_mutations.generate_user_mutation(pubkey))
    cleaned_return = raw_response["data"]["updateUserSettings"]
    return cleaned_return

def get_gpus() -> dict:
    '''
    Get all GPU types
    '''
    raw_response = run_graphql_query(gpus.QUERY_GPU_TYPES)
    cleaned_return = raw_response["data"]["gpuTypes"]
    return cleaned_return


def get_gpu(gpu_id : str, gpu_quantity : int = 1):
    '''
    Get a specific GPU type

    :param gpu_id: the id of the gpu
    :param gpu_quantity: how many of the gpu should be returned
    '''
    raw_response = run_graphql_query(gpus.generate_gpu_query(gpu_id, gpu_quantity))

    cleaned_return = raw_response["data"]["gpuTypes"]

    if len(cleaned_return) < 1:
        raise ValueError("No GPU found with the specified ID, "
                         "run runpod.get_gpus() to get a list of all GPUs")

    return cleaned_return[0]

def get_pods() -> dict:
    '''
    Get all pods
    '''
    raw_return = run_graphql_query(pod_queries.QUERY_POD)
    cleaned_return = raw_return["data"]["myself"]["pods"]
    return cleaned_return

def get_pod(pod_id : str):
    '''
    Get a specific pod

    :param pod_id: the id of the pod
    '''
    raw_response = run_graphql_query(pod_queries.generate_pod_query(pod_id))
    return raw_response["data"]["pod"]

def create_pod(
        name:str, image_name:str, gpu_type_id:str,
        cloud_type:str="ALL", support_public_ip:bool=True,
        start_ssh:bool=True,
        data_center_id : Optional[str]=None, country_code:Optional[str]=None,
        gpu_count:int=1, volume_in_gb:int=0, container_disk_in_gb:Optional[int]=None,
        min_vcpu_count:int=1, min_memory_in_gb:int=1, docker_args:str="",
        ports:Optional[str]=None, volume_mount_path:str="/runpod-volume",
        env:Optional[dict]=None,  template_id:Optional[str]=None,
        network_volume_id:Optional[str]=None
    ) -> dict:
    '''
    Create a pod

    :param name: the name of the pod
    :param image_name: the name of the docker image to be used by the pod
    :param gpu_type_id: the gpu type wanted by the pod (retrievable by get_gpus)
    :param cloud_type: if secure cloud, community cloud or all is wanted
    :param data_center_id: the id of the data center
    :param country_code: the code for country to start the pod in
    :param gpu_count: how many gpus should be attached to the pod
    :param volume_in_gb: how big should the pod volume be
    :param ports: the ports to open in the pod, example format - "8888/http,666/tcp"
    :param volume_mount_path: where to mount the volume?
    :param env: the environment variables to inject into the pod,
                for example {EXAMPLE_VAR:"example_value", EXAMPLE_VAR2:"example_value 2"}, will
                inject EXAMPLE_VAR and EXAMPLE_VAR2 into the pod with the mentioned values
    :param template_id: the id of the template to use for the pod

    :example:

    >>> pod_id = runpod.create_pod("test", "runpod/stack", "NVIDIA GeForce RTX 3070")
    '''
    # Input Validation
    get_gpu(gpu_type_id) # Check if GPU exists, will raise ValueError if not.
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
            name, image_name, gpu_type_id,
            cloud_type, support_public_ip, start_ssh,
            data_center_id, country_code, gpu_count,
            volume_in_gb, container_disk_in_gb, min_vcpu_count, min_memory_in_gb, docker_args,
            ports, volume_mount_path, env, template_id, network_volume_id)
    )

    cleaned_response = raw_response["data"]["podFindAndDeployOnDemand"]
    return cleaned_response


def stop_pod(pod_id: str):
    '''
    Stop a pod

    :param pod_id: the id of the pod

    :example:

    >>> pod_id = runpod.create_pod("test", "runpod/stack", "NVIDIA GeForce RTX 3070")
    >>> runpod.stop_pod(pod_id)
    '''
    raw_response = run_graphql_query(
        pod_mutations.generate_pod_stop_mutation(pod_id)
    )

    cleaned_response = raw_response["data"]["podStop"]
    return cleaned_response


def resume_pod(pod_id: str, gpu_count: int):
    '''
    Resume a pod

    :param pod_id: the id of the pod
    :param gpu_count: the number of GPUs to attach to the pod

    :example:

    >>> pod_id = runpod.create_pod("test", "runpod/stack", "NVIDIA GeForce RTX 3070")
    >>> runpod.stop_pod(pod_id)
    >>> runpod.resume_pod(pod_id)
    '''
    raw_response = run_graphql_query(
        pod_mutations.generate_pod_resume_mutation(pod_id, gpu_count)
    )

    cleaned_response = raw_response["data"]["podResume"]
    return cleaned_response


def terminate_pod(pod_id: str):
    '''
    Terminate a pod

    :param pod_id: the id of the pod

    :example:

    >>> pod_id = runpod.create_pod("test", "runpod/stack", "NVIDIA GeForce RTX 3070")
    >>> runpod.terminate_pod(pod_id)
    '''
    run_graphql_query(
        pod_mutations.generate_pod_terminate_mutation(pod_id)
    )
