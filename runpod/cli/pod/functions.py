'''
RunPod | CLI | Pod | Functions
'''
import os
import subprocess
import configparser

from runpod import get_pod, create_pod
from runpod.cli.utils import get_ssh_ip_port

SSH_KEY_FOLDER = os.path.expanduser('~/.runpod/ssh')

def pod_from_template(template_file):
    '''
    Creates a pod from a template file.
    '''
    pod_config = configparser.ConfigParser()
    pod_config.read(template_file)
    new_pod = create_pod(
        pod_config['pod'].pop('name'), pod_config['pod'].pop('image'),
        pod_config['pod'].pop('gpu_type'), **pod_config['pod'])

    return new_pod['id']


def open_ssh_connection(pod_id):
    '''
    Opens an SSH connection to a pod.
    '''
    pod = get_pod(pod_id)
    pod_ip, pod_port = get_ssh_ip_port(pod)


    key_files = []
    for file in os.listdir(SSH_KEY_FOLDER):
        if os.path.isfile(os.path.join(SSH_KEY_FOLDER, file)):
            key_files.append(file)


    cmd = ["ssh" , "-p", str(pod_port), "-o", "StrictHostKeyChecking=no"]
    for key_file in key_files:
        cmd.extend(["-i", os.path.join(SSH_KEY_FOLDER, key_file)])
    cmd.append(f"root@{pod_ip}")

    subprocess.run(cmd, check=True)
