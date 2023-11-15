'''
RunPod | CLI | Utils | SSH Command

Connect and run commands over SSH.
'''
import sys
import signal
import threading
import subprocess
import colorama
import paramiko

from runpod.cli import STOP_EVENT
from .rp_info import get_pod_ssh_ip_port
from .rp_userspace import find_ssh_key_file
from .rp_runpodignore import get_ignore_list

colorama.init(autoreset=True)  # Initialize colorama


class SSHConnection:
    ''' Connect and run commands over SSH. '''

    def __init__(self, pod_id):
        self.pod_id = pod_id

        try:
            self.pod_ip, self.pod_port = get_pod_ssh_ip_port(pod_id)
            self.key_file = find_ssh_key_file(self.pod_ip, self.pod_port)

            self.ssh = paramiko.SSHClient()
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(self.pod_ip, port=self.pod_port,
                             username='root', key_filename=self.key_file)
        except paramiko.SSHException as err:
            print(colorama.Fore.RED + f"[{pod_id}]", err)
            sys.exit(1)

        signal.signal(signal.SIGINT, self._signal_handler)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        del exc_type, exc_value, traceback
        self.close()

    def _get_ssh_options(self):
        """ Get the SSH options for connecting to the pod. """
        return [
            "-o", "StrictHostKeyChecking=no",
            "-o", "LogLevel=ERROR",
            "-p", str(self.pod_port),
            "-i", self.key_file
        ]

    def _signal_handler(self, signum, frame):
        ''' Handle signals. '''
        del signum, frame
        self.close()
        print(colorama.Fore.BLUE + f"[{self.pod_id}]", "SSH Connection Closed")
        STOP_EVENT.set()
        sys.exit(0)

    def run_commands(self, commands):
        ''' Runs a list of bash commands over SSH. '''
        def handle_stream(stream, color, prefix):
            for line in stream:
                if line:
                    print(color + f"[{prefix}]", line.strip())

        for command in commands:
            full_command = ' && '.join([
                'source /root/.bashrc',
                'source /etc/rp_environment',
                'while IFS= read -r -d \'\' line; do export "$line"; done < /proc/1/environ',
                command
            ])
            _, stdout, stderr = self.ssh.exec_command(full_command)

            stdout_thread = threading.Thread(
                target=handle_stream, args=(stdout, colorama.Fore.GREEN, self.pod_id), daemon=True)
            stderr_thread = threading.Thread(
                target=handle_stream, args=(stderr, colorama.Fore.RED, self.pod_id), daemon=True)

            stdout_thread.start()
            stderr_thread.start()

            stdout_thread.join()
            stderr_thread.join()

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
        cmd = ["ssh"] + self._get_ssh_options() + [f"root@{self.pod_ip}"]

        subprocess.run(cmd, check=True)

    def rsync(self, local_path, remote_path, quiet=False):
        """ Sync a local directory to a remote directory over SSH.

        A .runpodignore file can be used to ignore files and directories.
        This file should be placed in the root of the local directory to sync.

        Args:
            local_path (str): The local directory to sync.
            remote_path (str): The remote directory to sync.
        """
        rsync_cmd = ["rsync", "-avz", "--no-owner", "--no-group"]

        for pattern in get_ignore_list():
            rsync_cmd.extend(["--exclude", pattern])

        if quiet:
            rsync_cmd.append("--quiet")

        rsync_cmd.extend([
            "-e", f"ssh {' '.join(self._get_ssh_options())}",
            local_path,
            f"root@{self.pod_ip}:{remote_path}"
        ])

        return subprocess.run(rsync_cmd, check=True)


    def close(self):
        ''' Close the SSH connection. '''
        self.ssh.close()
