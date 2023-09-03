'''
RunPod | CLI | Exec | Functions
'''
import os
import logging

import paramiko

from runpod import SSH_KEY_FOLDER, get_pod
from runpod.cli.utils import get_ssh_ip_port

logging.basicConfig()
logging.getLogger("paramiko").setLevel(logging.WARNING)


def python_over_ssh(pod_id, file):
    '''
    Runs a Python file over SSH.
    '''
    pod = get_pod(pod_id)
    pod_ip, pod_port = get_ssh_ip_port(pod)

    key_files = []
    for key_file in os.listdir(SSH_KEY_FOLDER):
        if os.path.isfile(os.path.join(SSH_KEY_FOLDER, key_file)):
            key_files.append(key_file)

    # Connect to the pod
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    for key_file in key_files:
        try:
            ssh.connect(pod_ip, port=pod_port, username='root',
                        key_filename=os.path.join(SSH_KEY_FOLDER, key_file))
            break
        except paramiko.ssh_exception.SSHException:
            pass
        except Exception as err: # pylint: disable=broad-except
            print(f"An error occurred with key {key_file}: {err}")

    else:
        print("Failed to connect using all available keys.")
        return

    # Setup sftp connection and upload the file
    sftp = ssh.open_sftp()
    sftp.put(file, f'/root/{file}')
    sftp.close()

    # Run the file
    stdout = ssh.exec_command(f'python /root/{file}')[1]
    for line in stdout:
        print(line)

    ssh.close()
