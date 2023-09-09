'''
RunPod | CLI | Project | Functions
'''

import os
import shutil
import uuid
from configparser import ConfigParser

from runpod import create_pod, get_pod, get_pods
from runpod.cli.utils.ssh_cmd import SSHConnection
from ...utils.rp_sync import sync_directory

STARTER_TEMPLATES = os.path.join(os.path.dirname(__file__), 'starter_templates')

# -------------------------------- New Project ------------------------------- #
def create_new_project(project_name, runpod_volume_id, python_version,
                       model_type=None, model_name=None):
    '''
    Create a new project with the given name.
    '''
    project_folder = os.path.join(os.getcwd(), project_name)
    if not os.path.exists(project_folder):
        os.makedirs(project_folder)

    if model_type is None:
        model_type = "default"

    template_dir = os.path.join(STARTER_TEMPLATES, model_type)

    for item in os.listdir(template_dir):
        source_item = os.path.join(template_dir, item)
        destination_item = os.path.join(project_folder, item)

        if os.path.isdir(source_item):
            shutil.copytree(source_item, destination_item)
        else:
            shutil.copy2(source_item, destination_item)

    # If there's a model_name, replace placeholders in handler.py
    if model_name:
        handler_path = os.path.join(project_folder, "handler.py")
        if os.path.exists(handler_path):
            with open(handler_path, 'r', encoding='utf-8') as file:
                handler_content = file.read()

            handler_content = handler_content.replace('<<MODEL_NAME>>', model_name)

            with open(handler_path, 'w', encoding='utf-8') as file:
                file.write(handler_content)

    config = ConfigParser()

    config['PROJECT'] = {
        'UUID': str(uuid.uuid4())[:8],  # Short UUID
        'Name': project_name,
        'BaseImage': 'runpod/base:0.0.0',
        'GPU': 'NVIDIA RTX A4500',
        'GPUCount': 1,
        'StorageID': runpod_volume_id,
        'VolumeMountPath': '/runpod-volume',
        'Ports': '8080/http, 22/tcp',
        'ContainerDiskSizeGB': 10
    }

    config['TEMPLATE'] = {
        'ModelType': str(model_type),
        'ModelName': str(model_name)
    }

    config['ENVIRONMENT'] = {
        'PythonVersion': python_version,
        'RequirementsPath': os.path.join(project_folder, 'requirements.txt')
    }

    with open(os.path.join(project_folder, "runpod.toml"), 'w', encoding="UTF-8") as config_file:
        config.write(config_file)


# ------------------------------ Launch Project ------------------------------ #
def launch_project():
    '''
    Launch the project development environment from runpod.toml
    # SSH into the pod and create a project folder within the volume
    # Create a folder in the volume for the project that matches the project UUID
    # Create a folder in the project folder that matches the project name
    # Copy the project files into the project folder
    # crate a virtual environment using the python version specified in the project config
    # install the requirements.txt file
    '''
    project_file = os.path.join(os.getcwd(), 'runpod.toml')
    if not os.path.exists(project_file):
        raise FileNotFoundError("runpod.toml not found in the current directory.")

    with open(project_file, 'r', encoding="UTF-8") as config_file:
        config = ConfigParser()
        config.read_file(config_file)

    for config_item in config['PROJECT']:
        print(f'{config_item}: {config["PROJECT"][config_item]}')

    print("Launching pod on RunPod...")

    new_pod = create_pod(
        f'{config["PROJECT"]["Name"]}-dev ({config["PROJECT"]["UUID"]})',
        config['PROJECT']['BaseImage'],
        config['PROJECT']['GPU'],
        gpu_count=int(config['PROJECT']['GPUCount']),
        support_public_ip=True,
        ports=f'{config["PROJECT"]["Ports"]}',
        network_volume_id=f'{config["PROJECT"]["StorageID"]}',
        volume_mount_path=f'{config["PROJECT"]["VolumeMountPath"]}',
        container_disk_in_gb=int(config["PROJECT"]["ContainerDiskSizeGB"]),
        env={"RUNPOD_PROJECT_ID": config["PROJECT"]["UUID"]}
    )

    while new_pod.get('desiredStatus', None) != 'RUNNING' or new_pod.get('runtime', None) is None:
        new_pod = get_pod(new_pod['id'])

    print(f"Project {config['PROJECT']['Name']} pod ({new_pod['id']}) created.")

    ssh_conn = SSHConnection(new_pod['id'])
    project_files = os.listdir(os.getcwd())

    project_path_uuid = f'{config["PROJECT"]["VolumeMountPath"]}/{config["PROJECT"]["UUID"]}'
    project_path = f'{project_path_uuid}/{config["PROJECT"]["Name"]}'

    print(f'Creating project folder: {project_path} on pod {new_pod["id"]}')
    ssh_conn.run_commands([f'mkdir -p {project_path}'])

    for file in project_files:
        if os.path.isdir(file):
            ssh_conn.put_directory(file, f'{project_path}/{file}')
        else:
            ssh_conn.put_file(file, f'{project_path}/{file}')

    venv_path = os.path.join(project_path_uuid, "venv")

    print(f'Creating virtual environment: {venv_path} on pod {new_pod["id"]}')
    commands = [
        f'python{config["ENVIRONMENT"]["PythonVersion"]} -m venv {venv_path}',
        f'source {venv_path}/bin/activate &&' \
        f'cd {project_path} &&' \
        'pip install --upgrade pip &&'
        'pip install -r requirements.txt'
    ]

    ssh_conn.run_commands(commands)


# ------------------------------- Start Project ------------------------------ #
def start_project_api():
    '''
    python handler.py --rp_serve_api --rp_api_host="0.0.0.0" --rp_api_port=8080
    '''
    project_file = os.path.join(os.getcwd(), 'runpod.toml')
    if not os.path.exists(project_file):
        raise FileNotFoundError("runpod.toml not found in the current directory.")

    with open(project_file, 'r', encoding="UTF-8") as config_file:
        config = ConfigParser()
        config.read_file(config_file)

    # Get the project pod.
    for pod in get_pods():
        if config['PROJECT']['UUID'] in pod['name']:
            project_pod = pod
            break
    else:
        raise ValueError(f'Project pod not found for UUID: {config["PROJECT"]["UUID"]}')

    ssh_conn = SSHConnection(project_pod['id'])

    volume_mount_path = config["PROJECT"]["VolumeMountPath"]
    project_uuid = config["PROJECT"]["UUID"]
    project_name = config["PROJECT"]["Name"]
    remote_project_path = f'{volume_mount_path}/{project_uuid}/{project_name}'

    # ssh_conn.rsync(os.path.join(os.getcwd(), ''), remote_project_path)
    sync_directory(ssh_conn, os.getcwd(), remote_project_path)

    launch_api_server = [f'''
        function cleanup {{
            echo "Cleaning up..."
            kill $last_pid 2>/dev/null
        }}

        trap cleanup EXIT

        if source {volume_mount_path}/{project_uuid}/venv/bin/activate; then
            echo "Activated virtual environment."
        else
            echo "Failed to activate virtual environment."
            exit 1
        fi

        if cd {volume_mount_path}/{project_uuid}/{project_name}; then
            echo "Changed to project directory."
        else
            echo "Failed to change directory."
            exit 1
        fi

        python handler.py --rp_serve_api --rp_api_host="0.0.0.0" --rp_api_port=8080 &
        last_pid=$!
        echo "Started API server with PID: $last_pid"

        while true; do
            if changed_file=$(inotifywait -r -e modify,create,delete --exclude '(__pycache__|\\.pyc$)' {remote_project_path} --format '%w%f'); then
                echo "Detected changes in: $changed_file"
            else
                echo "Failed to detect changes."
                exit 1
            fi

            if kill $last_pid; then
                echo "Killed API server with PID: $last_pid"
            else
                echo "Failed to kill server."
                exit 1
            fi

            sleep 1

            python handler.py --rp_serve_api --rp_api_host="0.0.0.0" --rp_api_port=8080 &
            last_pid=$!
            echo "Restarted API server with PID: $last_pid"
        done
    ''']



    try:
        ssh_conn.run_commands(launch_api_server)
    finally:
        ssh_conn.close()
