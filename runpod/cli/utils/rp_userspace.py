'''
RunPod | CLI | Utils | Userspace
'''
import os
import paramiko
from runpod import SSH_KEY_PATH


def find_ssh_key_file(pod_ip, pod_port):
    """Find the SSH key file that can be used to connect to the pod.

    - Try all the keys in the SSH_KEY_PATH directory
    - If none of the keys work, return None
    - If multiple keys work, return the first one that works
    - Returns the path to the key file
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    for file in os.listdir(SSH_KEY_PATH):
        file_path = os.path.join(SSH_KEY_PATH, file)

        if not os.path.isfile(file_path) or file.endswith('.pub'):
            continue

        try:
            ssh.connect(pod_ip, port=pod_port, username='root', key_filename=file_path)
            ssh.close()
            print(f"Connected to pod {pod_ip}:{pod_port} using key {file}")
            return file_path
        except Exception as err:  # pylint: disable=broad-except
            print(f"An error occurred with key {file}: {err}")

    print("Failed to connect using all available keys.")
    return None
