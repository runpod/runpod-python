"""
RunPod | API Wrapper | Mutations | Pods
"""
# pylint: disable=too-many-arguments, too-many-locals, too-many-branches


def generate_pod_deployment_mutation(
        name:str, image_name:str, gpu_type_id:str,
        cloud_type:str="ALL", support_public_ip:bool=True, start_ssh:bool=True,
        data_center_id=None, country_code=None,
        gpu_count=None, volume_in_gb=None, container_disk_in_gb=None, min_vcpu_count=None,
        min_memory_in_gb=None, docker_args=None, ports=None, volume_mount_path=None,
        env:dict=None, template_id=None, network_volume_id=None):
    '''
    Generates a mutation to deploy a pod on demand.
    '''
    input_fields = []

    # ------------------------------ Required Fields ----------------------------- #
    input_fields.append(f'name: "{name}"')
    input_fields.append(f'imageName: "{image_name}"')
    input_fields.append(f'gpuTypeId: "{gpu_type_id}"')

    # ------------------------------ Default Fields ------------------------------ #
    input_fields.append(f'cloudType: {cloud_type}')

    if start_ssh:
        input_fields.append('startSsh: true')

    if support_public_ip:
        input_fields.append('supportPublicIp: true')
    else:
        input_fields.append('supportPublicIp: false')

    # ------------------------------ Optional Fields ----------------------------- #
    if data_center_id is not None:
        input_fields.append(f'dataCenterId: "{data_center_id}"')
    if country_code is not None:
        input_fields.append(f'countryCode: "{country_code}"')
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
    if docker_args is not None:
        input_fields.append(f'dockerArgs: "{docker_args}"')
    if ports is not None:
        ports = ports.replace(" ", "")
        input_fields.append(f'ports: "{ports}"')
    if volume_mount_path is not None:
        input_fields.append(f'volumeMountPath: "{volume_mount_path}"')
    if env is not None:
        env_string = ", ".join(
            [f'{{ key: "{key}", value: "{value}" }}' for key, value in env.items()])
        input_fields.append(f"env: [{env_string}]")
    if template_id is not None:
        input_fields.append(f'templateId: "{template_id}"')

    if network_volume_id is not None:
        input_fields.append(f'networkVolumeId: "{network_volume_id}"')

    # Format input fields
    input_string = ", ".join(input_fields)

    return f"""
    mutation {{
      podFindAndDeployOnDemand(
        input: {{
          {input_string}
        }}
      ) {{
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
