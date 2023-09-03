'''
RunPod | CLI | Utils | Userspace
'''
import os
import click

POD_ID_FILE = os.path.join(os.path.expanduser('~'), '.runpod', 'pod_id')

def get_or_prompt_for_pod_id():
    '''Retrieves the stored pod_id or prompts the user to provide one.'''
    if os.path.exists(POD_ID_FILE):
        with open(POD_ID_FILE, 'r', encoding="UTF-8") as pod_file:
            return pod_file.read().strip()

    # If file doesn't exist or is empty, prompt user for the pod_id
    pod_id = click.prompt('Please provide the pod ID')
    os.makedirs(os.path.dirname(POD_ID_FILE), exist_ok=True)
    with open(POD_ID_FILE, 'w', encoding="UTF-8") as pod_file:
        pod_file.write(pod_file)
    return pod_id
