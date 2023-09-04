import os
import shutil
import uuid
from configparser import ConfigParser

from runpod import create_pod, get_pod, get_pods
from runpod.cli.utils.ssh_cmd import SSHConnection

PROJECT_TEMPLATE_FOLDER = os.path.join(os.path.dirname(__file__), 'template')

def create_new_project(project_name, runpod_volume_id, python_version):
    '''
    Create a new project with the given name.
    '''
    project_folder = os.path.join(os.getcwd(), project_name)
    if not os.path.exists(project_folder):
        os.makedirs(project_folder)

    for item in os.listdir(PROJECT_TEMPLATE_FOLDER):
        source_item = os.path.join(PROJECT_TEMPLATE_FOLDER, item)
        destination_item = os.path.join(project_folder, item)

        if os.path.isdir(source_item):
            shutil.copytree(source_item, destination_item)
        else:
            shutil.copy2(source_item, destination_item)

    config = ConfigParser()

    config['PROJECT'] = {
        'UUID': str(uuid.uuid4())[:8],  # Short UUID
        'Name': project_name,
        'BaseImage': 'runpod/pytorch:2.0.1-py3.10-cuda11.8.0-devel',
        'StorageID': runpod_volume_id,
        'VolumeMountPath': '/runpod_volume',
        'Ports': '8080/http, 22/tcp',
        'ContainerDiskSizeGB': 10
    }

    config['ENVIRONMENT'] = {
        'PythonVersion': python_version,
        'RequirementsPath': os.path.join(project_folder, 'requirements.txt')
    }

    with open(os.path.join(project_folder, "runpod.toml"), 'w', encoding="UTF-8") as config_file:
        config.write(config_file)

def launch_project(project_file):
    '''
    Launch the project development environment from runpod.toml
    '''
    with open(project_file, 'r', encoding="UTF-8") as config_file:
        config = ConfigParser()
        config.read_file(config_file)

    for config_item in config['PROJECT']:
        print(f'{config_item}: {config["PROJECT"][config_item]}')

    project_pod = create_pod(
        f'{config["PROJECT"]["Name"]}-dev ({config["PROJECT"]["UUID"]})',
        'runpod/pytorch:2.0.1-py3.10-cuda11.8.0-devel',
        'NVIDIA GeForce RTX 4090',
        gpu_count=1,
        support_public_ip=True,
        ports=f'{config["PROJECT"]["Ports"]}',
        network_volume_id=f'{config["PROJECT"]["StorageID"]}',
        volume_mount_path=f'{config["PROJECT"]["VolumeMountPath"]}',
        container_disk_in_gb=int(config["PROJECT"]["ContainerDiskSizeGB"])
    )

    new_pod= get_pod(project_pod['id'])
    while new_pod['desiredStatus'] != 'RUNNING' or new_pod['runtime'] is None:
        new_pod = get_pod(project_pod['id'])

    print(f"Project {config['PROJECT']['Name']} launched successfully!")

    # SSH into the pod and create a project folder within the volume
    # Create a folder in the volume for the project that matches the project UUID
    # Create a folder in the project folder that matches the project name
    # Copy the project files into the project folder
    # crate a virtual environment using the python version specified in the project config
    # install the requirements.txt file

    ssh_conn = SSHConnection(project_pod['id'])

    current_dir = os.getcwd()
    project_files = os.listdir(current_dir)

    project_path_uuid = f'{config["PROJECT"]["VolumeMountPath"]}/{config["PROJECT"]["UUID"]}'
    project_path = f'{project_path_uuid}/{config["PROJECT"]["Name"]}'

    command_list = [
        f'mkdir -p {project_path}',
    ]

    ssh_conn.run_commands(command_list)

    for file in project_files:
        local_path = os.path.join(current_dir, file)
        remote_path = f'{project_path}/{file}'
        if os.path.isdir(local_path):
            ssh_conn.put_directory(local_path, remote_path)
        else:
            ssh_conn.put_file(local_path, remote_path)

    venv_path = os.path.join(project_path_uuid, "venv")
    python_version = config["ENVIRONMENT"]["PythonVersion"]
    commands = [
        f'python{python_version} -m venv {venv_path}',
        f'source {venv_path}/bin/activate &&' \
        f'cd {project_path} &&' \
        'pip install -r requirements.txt'
    ]

    ssh_conn.run_commands(commands)


def start_project_api(project_file):
    '''
    python handler.py --rp_serve_api --rp_api_host="0.0.0.0" --rp_api_port=8080
    '''
    with open(project_file, 'r', encoding="UTF-8") as config_file:
        config = ConfigParser()
        config.read_file(config_file)

    user_pods = get_pods()

    for pod in user_pods:
        if config['PROJECT']['UUID'] in pod['name']:
            project_pod = pod
            break

    ssh_conn = SSHConnection(project_pod['id'])
    launch_api_server = [
        f'source {config["PROJECT"]["VolumeMountPath"]}/{config["PROJECT"]["UUID"]}/venv/bin/activate &&' \
        f'cd {config["PROJECT"]["VolumeMountPath"]}/{config["PROJECT"]["UUID"]}/{config["PROJECT"]["Name"]} &&' \
        'python handler.py --rp_serve_api --rp_api_host="0.0.0.0" --rp_api_port=8080'
    ]

    ssh_conn.run_commands(launch_api_server)
