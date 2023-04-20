"""
RunPod | API Wrapper | CTL Commands
"""
# pylint: disable=too-many-arguments

from .queries import gpus
from .graphql import run_graphql_query
from .mutations import pods


def get_gpus():
    '''
    Get all GPU types
    '''
    raw_return = run_graphql_query(gpus.QUERY_GPU_TYPES)
    cleaned_return = raw_return["data"]["gpuTypes"]
    return cleaned_return


def get_gpu(gpu_id):
    '''
    Get a specific GPU type
    '''
    raw_return = run_graphql_query(gpus.generate_gpu_query(gpu_id))
    cleaned_return = raw_return["data"]["gpuTypes"][0]
    return cleaned_return


def create_pod(name, image_name, gpu_type_id, cloud_type="ALL", gpu_count=1, volume_in_gb=0,
               container_disk_in_gb=5, min_vcpu_count=1, min_memory_in_gb=1, docker_args="",
               ports=None, volume_mount_path="/workspace", env=None):
    '''
    Create a pod
    '''

    raw_response = run_graphql_query(
        pods.generate_pod_deployment_mutation(
            name, image_name, gpu_type_id, cloud_type, gpu_count, volume_in_gb,
            container_disk_in_gb, min_vcpu_count, min_memory_in_gb, docker_args,
            ports, volume_mount_path, env)
    )

    cleaned_response = raw_response["data"]["podFindAndDeployOnDemand"]
    return cleaned_response


def stop_pod(pod_id: str):
    '''
    Stop a pod
    '''
    raw_response = run_graphql_query(
        pods.generate_pod_stop_mutation(pod_id)
    )

    cleaned_response = raw_response["data"]["podStop"]
    return cleaned_response


def resume_pod(pod_id: str, gpu_count: int):
    '''
    Resume a pod
    '''
    raw_response = run_graphql_query(
        pods.generate_pod_resume_mutation(pod_id, gpu_count)
    )

    cleaned_response = raw_response["data"]["podResume"]
    return cleaned_response


def terminate_pod(pod_id: str):
    '''
    Terminate a pod
    '''
    run_graphql_query(
        pods.generate_pod_terminate_mutation(pod_id)
    )
