'''
RunPod | CLI | Utils | Userspace
'''
import os
import click
import paramiko
from runpod import SSH_KEY_FOLDER, get_pod

POD_ID_FILE = os.path.join(os.path.expanduser('~'), '.runpod', 'pod_id')


def get_or_prompt_for_pod_id():
    '''Retrieves the stored pod_id or prompts the user to provide one.'''
    if os.path.exists(POD_ID_FILE):
        with open(POD_ID_FILE, 'r', encoding="UTF-8") as pod_file:
            pod_id = pod_file.read().strip()

    # Confirm that the pod_id is valid
    if get_pod(pod_id) is not None:
        return pod_id

    # If file doesn't exist or is empty, prompt user for the pod_id
    pod_id = click.prompt('Please provide the pod ID')
    os.makedirs(os.path.dirname(POD_ID_FILE), exist_ok=True)
    with open(POD_ID_FILE, 'w', encoding="UTF-8") as pod_file:
        pod_file.write(pod_id)

    return pod_id


def find_ssh_key_file(pod_ip, pod_port):
    '''
    Finds the SSH key to use for the SSH connection.
    '''
    key_files = []
    for file in os.listdir(SSH_KEY_FOLDER):
        if os.path.isfile(os.path.join(SSH_KEY_FOLDER, file)) and not file.endswith('.pub'):
            key_files.append(file)

    # Connect to the pod
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    for key_file in key_files:
        try:
            ssh.connect(pod_ip, port=pod_port, username='root',
                        key_filename=os.path.join(SSH_KEY_FOLDER, key_file))
            ssh.close()
            return key_file
        except paramiko.ssh_exception.SSHException:
            pass
        except Exception as err: # pylint: disable=broad-except
            print(f"An error occurred with key {key_file}: {err}")

    print("Failed to connect using all available keys.")
    return None
