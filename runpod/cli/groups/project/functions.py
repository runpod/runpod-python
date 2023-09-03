import os
import shutil
import uuid
from configparser import ConfigParser

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
