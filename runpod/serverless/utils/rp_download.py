'''
PodWorker | modules | download.py

Called when inputs are images or zip files.
Downloads them into a temporary directory called "input_objects".
This directory is cleaned up after the job is complete.
'''

import os
import re
import uuid
import zipfile
from urllib.parse import urlparse

import requests


def download_input_objects(object_locations):
    '''
    Cycles through the object locations and downloads them.
    Returns the list of downloaded objects paths.
    '''
    os.makedirs('input_objects', exist_ok=True)

    objects = []
    for object_url in object_locations:
        if object_url is None:
            objects.append(None)
            continue

        response = requests.get(object_url, timeout=5)
        object_path = urlparse(object_url).path

        file_name = os.path.basename(object_path)
        file_extension = os.path.splitext(file_name)[1]

        object_name = f'{uuid.uuid4()}{file_extension}'

        with open(f'input_objects/{object_name}', 'wb') as output_file:
            output_file.write(response.content)

        objects.append(f'input_objects/{object_name}')

    return objects


def file(file_url):
    '''
    Downloads a single file from a given URL, file is given a random name.
    First checks if the content-disposition header is set, if so, uses the file name from there.
    If the file is a zip file, it is extracted into a directory with the same name.

    Returns an object that contains:
    - The absolute path to the downloaded file
    - File type
    - Original file name
    '''
    os.makedirs('job_files', exist_ok=True)

    download_response = requests.get(file_url, timeout=30)

    original_file_name = []
    if "Content-Disposition" in download_response.headers.keys():
        original_file_name = re.findall(
            "filename=(.+)",
            download_response.headers["Content-Disposition"]
        )

    if len(original_file_name) > 0:
        original_file_name = original_file_name[0]
    else:
        download_path = urlparse(file_url).path
        original_file_name = os.path.basename(download_path)

    file_type = os.path.splitext(original_file_name)[1].replace('.', '')

    file_name = f'{uuid.uuid4()}'

    with open(f'job_files/{file_name}.{file_type}', 'wb') as output_file:
        output_file.write(download_response.content)

    if file_type == 'zip':
        unziped_directory = f'job_files/{file_name}'
        os.makedirs(unziped_directory, exist_ok=True)
        with zipfile.ZipFile(f'job_files/{file_name}.{file_type}', 'r') as zip_ref:
            zip_ref.extractall(unziped_directory)
        unziped_directory = os.path.abspath(unziped_directory)
    else:
        unziped_directory = None

    return {
        "file_path": os.path.abspath(f'job_files/{file_name}.{file_type}'),
        "type": file_type,
        "original_name": original_file_name,
        "extracted_path": unziped_directory
    }
