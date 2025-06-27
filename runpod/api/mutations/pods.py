"""
Runpod | API Wrapper | Mutations | Pods
"""

# pylint: disable=too-many-arguments, too-many-locals, too-many-branches

from typing import List, Optional


def generate_pod_deployment_mutation(
    name: str,
    image_name: str,
    gpu_type_id: Optional[str] = None,
    cloud_type: str = "ALL",
    support_public_ip: bool = True,
    start_ssh: bool = True,
    data_center_id: Optional[str] = None,
    country_code: Optional[str] = None,
    gpu_count: Optional[int] = None,
    volume_in_gb: Optional[int] = None,
    container_disk_in_gb: Optional[int] = None,
    min_vcpu_count: Optional[int] = None,
    min_memory_in_gb: Optional[int] = None,
    docker_args: Optional[str] = None,
    ports: Optional[str] = None,
    volume_mount_path: Optional[str] = None,
    env: Optional[dict] = None,
    template_id: Optional[str] = None,
    network_volume_id: Optional[str] = None,
    allowed_cuda_versions: Optional[List[str]] = None,
    min_download: Optional[int] = None,
    min_upload: Optional[int] = None,
    instance_id: Optional[str] = None,
) -> str:
    """
    Generates a mutation to deploy a pod on demand.
    
    Args:
        name: Name of the pod
        image_name: Docker image name
        gpu_type_id: GPU type ID for GPU pods, None for CPU pods
        cloud_type: Cloud type (ALL, COMMUNITY, or SECURE)
        support_public_ip: Whether to support public IP
        start_ssh: Whether to start SSH service
        data_center_id: Data center ID
        country_code: Country code for pod location
        gpu_count: Number of GPUs (for GPU pods)
        volume_in_gb: Volume size in GB
        container_disk_in_gb: Container disk size in GB
        min_vcpu_count: Minimum vCPU count
        min_memory_in_gb: Minimum memory in GB
        docker_args: Docker arguments
        ports: Port mappings (e.g. "8080/tcp,22/tcp")
        volume_mount_path: Volume mount path
        env: Environment variables dict
        template_id: Template ID
        network_volume_id: Network volume ID
        allowed_cuda_versions: List of allowed CUDA versions
        min_download: Minimum download speed in Mbps
        min_upload: Minimum upload speed in Mbps
        instance_id: Instance ID for CPU pods

    Returns:
        str: GraphQL mutation string
    """
    input_fields = []

    # Required Fields
    input_fields.extend([
        f'name: "{name}"',
        f'imageName: "{image_name}"',
        f"cloudType: {cloud_type}"
    ])

    if start_ssh:
        input_fields.append("startSsh: true")

    # GPU Pod Fields
    if gpu_type_id is not None:
        input_fields.append(f'gpuTypeId: "{gpu_type_id}"')
        input_fields.append(f"supportPublicIp: {str(support_public_ip).lower()}")

        if gpu_count is not None:
            input_fields.append(f"gpuCount: {gpu_count}")
        if volume_in_gb is not None:
            input_fields.append(f"volumeInGb: {volume_in_gb}")
        if min_vcpu_count is not None:
            input_fields.append(f"minVcpuCount: {min_vcpu_count}")
        if min_memory_in_gb is not None:
            input_fields.append(f"minMemoryInGb: {min_memory_in_gb}")
        if docker_args is not None:
            input_fields.append(f'dockerArgs: "{docker_args}"')
        if allowed_cuda_versions is not None:
            cuda_versions = ", ".join(f'"{v}"' for v in allowed_cuda_versions)
            input_fields.append(f"allowedCudaVersions: [{cuda_versions}]")

    # CPU Pod Fields
    else:
        if instance_id is not None:
            input_fields.append(f'instanceId: "{instance_id}"')
        template_id = template_id or "runpod-ubuntu"

    # Optional Fields
    if data_center_id is not None:
        input_fields.append(f'dataCenterId: "{data_center_id}"')
    else:
        input_fields.append("dataCenterId: null")

    if country_code is not None:
        input_fields.append(f'countryCode: "{country_code}"')
    if container_disk_in_gb is not None:
        input_fields.append(f"containerDiskInGb: {container_disk_in_gb}")
    if ports is not None:
        input_fields.append(f'ports: "{ports.replace(" ", "")}"')
    if volume_mount_path is not None:
        input_fields.append(f'volumeMountPath: "{volume_mount_path}"')
    if env is not None:
        env_items = [f'{{ key: "{k}", value: "{v}" }}' for k, v in env.items()]
        input_fields.append(f"env: [{', '.join(env_items)}]")
    if template_id is not None:
        input_fields.append(f'templateId: "{template_id}"')
    if network_volume_id is not None:
        input_fields.append(f'networkVolumeId: "{network_volume_id}"')
    if min_download is not None:
        input_fields.append(f'minDownload: {min_download}')
    if min_upload is not None:
        input_fields.append(f'minUpload: {min_upload}')

    mutation_type = "podFindAndDeployOnDemand" if gpu_type_id else "deployCpuPod"
    input_string = ", ".join(input_fields)

    return f"""
    mutation {{
      {mutation_type}(
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
    """
    Generates a mutation to stop a pod.
    """
    return f"""
    mutation {{
        podStop(input: {{ podId: "{pod_id}" }}) {{
            id
            desiredStatus
        }}
    }}
    """


def generate_pod_resume_mutation(pod_id: str, gpu_count: int) -> str:
    """
    Generates a mutation to resume a pod.
    """
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
    """
    Generates a mutation to terminate a pod.
    """
    return f"""
    mutation {{
        podTerminate(input: {{ podId: "{pod_id}" }})
    }}
    """
