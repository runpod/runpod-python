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
    - Returns the name of the key file that can be used to connect to the pod
    """
    key_files = []
    for file in os.listdir(SSH_KEY_PATH):
        if os.path.isfile(os.path.join(SSH_KEY_PATH, file)) and not file.endswith('.pub'):
            key_files.append(file)

    # Connect to the pod
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    for key_file in key_files:
        try:
            ssh.connect(pod_ip, port=pod_port, username='root',
                        key_filename=os.path.join(SSH_KEY_PATH, key_file))
            ssh.close()
            return key_file
        except paramiko.ssh_exception.SSHException:
            pass
        except Exception as err: # pylint: disable=broad-except
            print(f"An error occurred with key {key_file}: {err}")

    print("Failed to connect using all available keys.")
    return None
