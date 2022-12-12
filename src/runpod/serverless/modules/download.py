'''
PodWorker | modules | download.py

Called when inputs are images or zip files.
Downloads them into a temporary directory called "input_objects".
This directory is cleaned up after the job is complete.
'''

import os
import uuid
import requests


def download_input_objects(object_locations):
    '''
    Cycles through the object locations and downloads them.
    Returns the list of downloaded objects paths.
    '''
    os.makedirs('input_objects', exist_ok=True)

    objects = []
    for object_url in object_locations:
        response = requests.get(object_url, timeout=5)

        file_name = os.path.basename(object_url)
        file_extension = os.path.splitext(file_name)[1]

        object_name = f'{uuid.uuid4()}{file_extension}'

        with open(f'input_objects/{object_name}', 'wb') as output_file:
            output_file.write(response.content)

        objects.append(f'input_objects/{object_name}')

    return objects
