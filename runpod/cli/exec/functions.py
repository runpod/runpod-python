'''
RunPod | CLI | Exec | Functions
'''
import os
import paramiko

from runpod import get_pod

SSH_KEY_FOLDER = os.path.expanduser('~/.runpod/ssh')

def python_over_ssh(pod_id, file):
    '''
    Runs a Python file over SSH.
    '''
    pod = get_pod(pod_id)

    if pod['desiredStatus'] == 'RUNNING':
        for port in pod['runtime']['ports']:
            if port['privatePort'] == 22:
                pod_ip = port['ip']
                pod_port = port['publicPort']

    key_files = [os.path.join(SSH_KEY_FOLDER, f) for f in os.listdir(SSH_KEY_FOLDER) if os.path.isfile(os.path.join(SSH_KEY_FOLDER, f))]

    # Connect to the pod
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    for key_file in key_files:
        try:
            ssh.connect(pod_ip, port=pod_port, username='root', key_filename=key_file)
            break
        except paramiko.ssh_exception.AuthenticationException:
            pass
        except Exception as e:
            print(f"An error occurred with key {key_file}: {e}")

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
