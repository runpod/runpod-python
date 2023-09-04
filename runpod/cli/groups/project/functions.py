import os
import shutil
import uuid
from configparser import ConfigParser

from runpod import create_pod, get_pod
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
    while new_pod['desiredStatus'] != 'RUNNING' and new_pod['runtime'] is not None:
        new_pod = get_pod(project_pod['id'])

    print(f"Project {config['PROJECT']['Name']} launched successfully!")

    # SSH into the pod and create a project folder within the volume
    # Create a folder in the volume for the project that matches the project UUID
    # Create a folder in the project folder that matches the project name
    # Copy the project files into the project folder
    # crate a virtual environment using the python version specified in the project config
    # install the requirements.txt file

    ssh_conn = SSHConnection(project_pod['id'])

    project_files = os.listdir(os.path.dirname(project_file))

    command_list = [
        f'mkdir -p {config["PROJECT"]["VolumeMountPath"]}/{config["PROJECT"]["UUID"]}',
        f'mkdir -p {config["PROJECT"]["VolumeMountPath"]}/{config["PROJECT"]["UUID"]}/{config["PROJECT"]["Name"]}',
    ]

    ssh_conn.run_commands(command_list)

    for file in project_files:
        ssh_conn.put_file(os.path.join(os.path.dirname(project_file), file), f'{config["PROJECT"]["VolumeMountPath"]}/{config["PROJECT"]["UUID"]}/{config["PROJECT"]["Name"]}/{file}')

    ssh_conn.run_commands([
        f'cd {config["PROJECT"]["VolumeMountPath"]}/{config["PROJECT"]["UUID"]}/{config["PROJECT"]["Name"]}',
        f'python{config["ENVIRONMENT"]["PythonVersion"]} -m venv venv'
    ])
