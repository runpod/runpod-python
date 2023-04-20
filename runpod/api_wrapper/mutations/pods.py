"""
RunPod | API Wrapper | Mutations | Pods
"""
# pylint: disable=too-many-arguments, too-many-locals, too-many-branches


def generate_pod_deployment_mutation(
        name, image_name, gpu_type_id, cloud_type=None, gpu_count=None,
        volume_in_gb=None, container_disk_in_gb=None, min_vcpu_count=None,
        min_memory_in_gb=None, docker_args=None, ports=None, volume_mount_path=None,
        env=None):
    '''
    Generates a mutation to deploy a pod on demand.
    '''
    input_fields = []

    if cloud_type is not None:
        input_fields.append(f"cloudType: {cloud_type}")
    if gpu_count is not None:
        input_fields.append(f"gpuCount: {gpu_count}")
    if volume_in_gb is not None:
        input_fields.append(f"volumeInGb: {volume_in_gb}")
    if container_disk_in_gb is not None:
        input_fields.append(f"containerDiskInGb: {container_disk_in_gb}")
    if min_vcpu_count is not None:
        input_fields.append(f"minVcpuCount: {min_vcpu_count}")
    if min_memory_in_gb is not None:
        input_fields.append(f"minMemoryInGb: {min_memory_in_gb}")
    if gpu_type_id is not None:
        input_fields.append(f'gpuTypeId: "{gpu_type_id}"')
    if name is not None:
        input_fields.append(f'name: "{name}"')
    if image_name is not None:
        input_fields.append(f'imageName: "{image_name}"')
    if docker_args is not None:
        input_fields.append(f'dockerArgs: "{docker_args}"')
    if ports is not None:
        input_fields.append(f'ports: "{ports}"')
    if volume_mount_path is not None:
        input_fields.append(f'volumeMountPath: "{volume_mount_path}"')
    if env is not None:
        env_string = ", ".join(
            [f'{{ key: "{key}", value: "{value}" }}' for key, value in env.items()])
        input_fields.append(f"env: [{env_string}]")

    input_string = ", ".join(input_fields)

    return f"""
    mutation {{
      podFindAndDeployOnDemand(
        input: {{
          {input_string}
        }}
      ) {{
        id
        imageName
        env
        machineId
        machine {{
          podHostId
        }}
      }}
    }}
    """


def generate_pod_stop_mutation(pod_id: str) -> str:
    '''
    Generates a mutation to stop a pod.
    '''
    return f"""
    mutation {{
        podStop(input: {{ podId: "{pod_id}" }}) {{
            id
            desiredStatus
        }}
    }}
    """


def generate_pod_resume_mutation(pod_id: str, gpu_count: int) -> str:
    '''
    Generates a mutation to resume a pod.
    '''
    return f"""
    mutation {{
        podResume(input: {{ podId: "{pod_id}", gpuCount: {gpu_count} }}) {{
            id
            desiredStatus
            imageName
            env
            machineId
            machine {{
                podHostId
            }}
        }}
    }}
    """


def generate_pod_terminate_mutation(pod_id: str) -> str:
    '''
    Generates a mutation to terminate a pod.
    '''
    return f"""
    mutation {{
        podTerminate(input: {{ podId: "{pod_id}" }})
    }}
    """
