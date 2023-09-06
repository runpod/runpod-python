'''
RunPod | CLI | Exec | Functions
'''
from runpod.cli.utils import ssh_cmd


def python_over_ssh(pod_id, file):
    '''
    Runs a Python file over SSH.
    '''
    ssh = ssh_cmd.SSHConnection(pod_id)
    ssh.put_file(file, f'/root/{file}')
    ssh.run_commands([f'python3.10 /root/{file}'])
    ssh.close()
