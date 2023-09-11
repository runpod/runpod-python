'''
RunPod | CLI | Utils | SSH Command

Connect and run commands over SSH.
'''
import os
import logging
import subprocess
import colorama
import paramiko

from runpod import SSH_KEY_FOLDER
from .pod_info import get_ssh_ip_port
from .userspace import find_ssh_key_file

logging.basicConfig()
logging.getLogger("paramiko").setLevel(logging.WARNING)


class SSHConnection:
    ''' Connect and run commands over SSH. '''

    def __init__(self, pod_id):
        self.pod_id = pod_id
        self.pod_ip, self.pod_port = get_ssh_ip_port(pod_id)
        self.key_file = find_ssh_key_file(self.pod_ip, self.pod_port)

        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(self.pod_ip, port=self.pod_port, username='root',
                         key_filename=os.path.join(SSH_KEY_FOLDER, self.key_file))

        colorama.init(autoreset=True) # Initialize colorama

    def run_commands(self, commands):
        ''' Runs a list of bash commands over SSH. '''
        for command in commands:
            command = f'source /root/.bashrc && {command}'
            command = f'source /etc/rp_environment && {command}'
            _, stdout, stderr = self.ssh.exec_command(command)
            for line in stdout:
                print(colorama.Fore.GREEN + f"[{self.pod_id}]", line.strip())
            for line in stderr:
                print(colorama.Fore.RED + f"[{self.pod_id} ERROR]", line.strip())

    def put_directory(self, local_path, remote_path):
        ''' Copy local directory to remote machine over SSH. '''
        with self.ssh.open_sftp() as sftp:
            try:
                sftp.stat(remote_path)
            except IOError:
                sftp.mkdir(remote_path)

            for file in os.listdir(local_path):
                local_file_path = os.path.join(local_path, file)
                remote_file_path = os.path.join(remote_path, file)
                if os.path.isdir(local_file_path):
                    try:
                        sftp.stat(remote_file_path)
                    except IOError:
                        sftp.mkdir(remote_file_path)
                    self.put_directory(local_file_path, remote_file_path)
                else:
                    sftp.put(local_file_path, remote_file_path)

    def put_file(self, local_path, remote_path):
        ''' Copy local file to remote machine over SSH. '''
        with self.ssh.open_sftp() as sftp:
            sftp.put(local_path, remote_path)

    def get_file(self, remote_path, local_path):
        ''' Fetch a remote file to local machine over SSH. '''
        with self.ssh.open_sftp() as sftp:
            sftp.get(remote_path, local_path)

    def launch_terminal(self):
        ''' Launch an interactive terminal over SSH. '''
        cmd = [
            "ssh" , "-p", str(self.pod_port),
            "-o", "StrictHostKeyChecking=no",
            "-i", os.path.join(SSH_KEY_FOLDER, self.key_file),
            f"root@{self.pod_ip}"
        ]

        subprocess.run(cmd, check=True)

    def rsync(self, local_path, remote_path):
        '''
        Sync a local directory to a remote directory over SSH.
        '''
        ssh_options = [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-p", str(self.pod_port),
            "-i", os.path.join(SSH_KEY_FOLDER, self.key_file)
        ]

        rsync_cmd = [
            "rsync", "-avz", "-no-owner", "-no-group",
            "--exclude", "__pycache__/", "--exclude", "*.pyc", "--exclude", ".*.swp",
            "-e", f"ssh {' '.join(ssh_options)}",
            local_path,
            f"root@{self.pod_ip}:{remote_path}"
        ]

        return subprocess.run(rsync_cmd, check=True)

    def close(self):
        ''' Close the SSH connection. '''
        self.ssh.close()
