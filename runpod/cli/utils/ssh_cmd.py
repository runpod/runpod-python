'''
RunPod | CLI | Utils | SSH Command

Connect and run commands over SSH.
'''
import os
import paramiko

from runpod import get_pod, SSH_KEY_FOLDER
from .pod_info import get_ssh_ip_port
from .userspace import find_ssh_key_file

class SSHConnection:
    def __init__(self, pod_id):
        self.pod = get_pod(pod_id)
        self.pod_ip, self.pod_port = get_ssh_ip_port(self.pod)
        self.key_file = find_ssh_key_file(self.pod_ip, self.pod_port)

        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(self.pod_ip, port=self.pod_port, username='root',
                         key_filename=os.path.join(SSH_KEY_FOLDER, self.key_file))

    def run_commands(self, commands):
        ''' Runs a list of bash commands over SSH. '''
        for command in commands:
            stdin, stdout, stderr = self.ssh.exec_command(command)
            for line in stdout:
                print(line.strip())  # Using strip() to remove leading/trailing whitespace

    def put_directory(self, local_path, remote_path):
        ''' Copy local directory to remote machine over SSH. '''
        with self.ssh.open_sftp() as sftp:
            # Check if the directory exists on the remote machine
            try:
                sftp.stat(remote_path)
            except IOError:
                # Directory doesn't exist and needs to be created
                sftp.mkdir(remote_path)

            for file in os.listdir(local_path):
                local_file_path = os.path.join(local_path, file)
                remote_file_path = os.path.join(remote_path, file)
                if os.path.isdir(local_file_path):
                    # The item is a directory, so create it and recurse into it
                    try:
                        sftp.stat(remote_file_path)
                    except IOError:
                        sftp.mkdir(remote_file_path)
                    self.put_directory(local_file_path, remote_file_path)
                else:
                    # The item is a file, so copy it
                    sftp.put(local_file_path, remote_file_path)

    def put_file(self, local_path, remote_path):
        ''' Copy local file to remote machine over SSH. '''
        with self.ssh.open_sftp() as sftp:
            sftp.put(local_path, remote_path)

    def get_file(self, remote_path, local_path):
        ''' Fetch a remote file to local machine over SSH. '''
        with self.ssh.open_sftp() as sftp:
            sftp.get(remote_path, local_path)

    def close(self):
        ''' Close the SSH connection. '''
        self.ssh.close()
