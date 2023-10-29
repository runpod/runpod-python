""" RunPod | API Wrapper | Mutations | Templates """

# pylint: disable=too-many-arguments

def generate_pod_template(
        name:str, image_name:str, docker_start_cmd:str=None,
        container_disk_in_gb:int=10, volume_in_gb:int=None, volume_mount_path:str=None,
        ports:str=None, env:dict=None, is_serverless:bool=False
):
    """ Generate a string for a GraphQL mutation to create a new pod template. """
    input_fields = []

    # ------------------------------ Required Fields ----------------------------- #
    input_fields.append(f'name: "{name}"')
    input_fields.append(f'imageName: "{image_name}"')

    # ------------------------------ Optional Fields ----------------------------- #
    if docker_start_cmd is not None:
        docker_start_cmd = docker_start_cmd.replace('"', '\\"')
        input_fields.append(f'dockerArgs: "{docker_start_cmd}"')
    else:
        input_fields.append('dockerArgs: ""')

    input_fields.append(f'containerDiskInGb: {container_disk_in_gb}')

    if volume_in_gb is not None:
        input_fields.append(f'volumeInGb: {volume_in_gb}')
    else:
        input_fields.append('volumeInGb: 0')

    if volume_mount_path is not None:
        input_fields.append(f'volumeMountPath: "{volume_mount_path}"')

    if ports is not None:
        ports = ports.replace(" ", "")
        input_fields.append(f'ports: "{ports}"')
    else:
        input_fields.append('ports: ""')

    if env is not None:
        env_string = ", ".join(
            [f'{{ key: "{key}", value: "{value}" }}' for key, value in env.items()])
        input_fields.append(f"env: [{env_string}]")
    else:
        input_fields.append('env: []')


    if is_serverless:
        input_fields.append('isServerless: true')
    else:
        input_fields.append('isServerless: false')

    # ------------------------------ Enforced Fields ----------------------------- #
    input_fields.append('startSsh: true')
    input_fields.append('isPublic: false')
    input_fields.append('readme: ""')

    # Format the input fields into a string
    input_fields_string = ", ".join(input_fields)

    return f"""
    mutation {{
        saveTemplate(
            input: {{
                {input_fields_string}
            }}
        ) {{
            id
            name
            imageName
            dockerArgs
            containerDiskInGb
            volumeInGb
            volumeMountPath
            ports
            env {{
                key
                value
            }}
            isServerless
        }}
    }}
    """
