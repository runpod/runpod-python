'''
RunPod | CLI | Pod | Functions
'''
import os
import subprocess

from runpod import get_pod

SSH_KEY_FOLDER = os.path.expanduser('~/.runpod/ssh')

def open_ssh_connection(pod_id):
    '''
    Opens an SSH connection to a pod.
    '''
    pod = get_pod(pod_id)

    if pod['desiredStatus'] == 'RUNNING':
        for port in pod['runtime']['ports']:
            if port['privatePort'] == 22:
                pod_ip = port['ip']
                pod_port = port['publicPort']



    key_files = []
    for file in os.listdir(SSH_KEY_FOLDER):
        if os.path.isfile(os.path.join(SSH_KEY_FOLDER, file)):
            key_files.append(file)


    cmd = ["ssh" , "-p", str(pod_port), "-o", "StrictHostKeyChecking=no"]
    for key_file in key_files:
        cmd.extend(["-i", os.path.join(SSH_KEY_FOLDER, key_file)])
    cmd.append(f"root@{pod_ip}")

    subprocess.run(cmd, check=True)
