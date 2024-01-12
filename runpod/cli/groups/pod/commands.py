'''
RunPod | CLI | Pod | Commands
'''
import click
import os
from prettytable import PrettyTable

from runpod import get_pods, create_pod

from ...utils import ssh_cmd

@click.group('pod', help='Manage and interact with pods.')
def pod_cli():
    '''A collection of CLI functions for Pod.'''

@pod_cli.command('list')
def list_pods():
    '''
    Lists the pods for the current user.
    '''
    table = PrettyTable(['ID', 'Name', 'Status', 'Image'])
    for pod in get_pods():
        table.add_row((pod['id'], pod['name'], pod['desiredStatus'], pod['imageName']))

    click.echo(table)

@pod_cli.command('create')
@click.argument('name', required=False)
@click.option('--image', default=None, help='The image to use for the pod.')
@click.option('--gpu-type', default=None, help='The GPU type to use for the pod.')
@click.option('--gpu-count', default=1, help='The number of GPUs to use for the pod.')
@click.option('--support-public-ip', default=True, help='Whether or not to support a public IP.')
def create_new_pod(name, image, gpu_type, gpu_count, support_public_ip): # pylint: disable=too-many-arguments
    '''
    Creates a pod.
    '''
    if not name:
        name = click.prompt('Enter pod name', default='RunPod-CLI-Pod')

    quick_launch = click.confirm('Would you like to launch default pod?', abort=True)
    if quick_launch:
        image = 'runpod/base:0.0.0'
        gpu_type = 'NVIDIA GeForce RTX 3090'
        ports ='22/tcp'

        click.echo('Launching default pod...')

    new_pod = create_pod(name, image, gpu_type,
                         gpu_count=gpu_count, support_public_ip=support_public_ip, ports=ports)

    click.echo(f'Pod {new_pod["id"]} has been created.')

@pod_cli.command('connect')
@click.argument('pod_id')
def connect_to_pod(pod_id):
    '''
    Connects to a pod.
    '''
    click.echo(f'Connecting to pod {pod_id}...')
    ssh = ssh_cmd.SSHConnection(pod_id)
    ssh.launch_terminal()

@pod_cli.command("send")
@click.argument("pod_id", required=True)
@click.argument(
    "local_path",
    required=True,
    type=click.Path(exists=True, file_okay=True, dir_okay=False),
)
@click.argument("remote_path", required=True)
def send_file(pod_id, local_path, remote_path):
    """
    Send a local file to a specified pod.
    ...
    """
    try:
        absolute_local_path = os.path.abspath(local_path)

        if not os.path.isfile(absolute_local_path):
            raise ValueError(f"The local path '{absolute_local_path}' is not a file.")

        # Assuming the remote path is relative to the user's home directory
        remote_directory = os.path.dirname(remote_path)
        if remote_directory.startswith("."):
            remote_directory = remote_directory[1:]  # Remove './' if present
        remote_directory_display = f"{remote_directory}" if remote_directory else "~"

        click.echo(
            f"Sending file from {absolute_local_path} to pod {pod_id}:{remote_path}..."
        )
        with ssh_cmd.SSHConnection(pod_id) as ssh:
            ssh.put_file(absolute_local_path, remote_path)
        click.echo(
            f"File sent successfully to {remote_directory_display} on pod {pod_id}."
        )
        click.echo(f"To access the file, use: cd {remote_directory_display}. Type pwd to make sure you get put in the right directory.")

    except Exception as e:
        click.echo(f"Failed to send file: {e}", err=True)


@pod_cli.command("download")
@click.argument("pod_id", required=True)
@click.argument("remote_path", required=True)
@click.argument("local_path", required=True)
def download_file(pod_id, remote_path, local_path):
    """
    Download a file from a specified pod to local machine.
    ...
    """
    try:
        absolute_local_path = os.path.abspath(local_path)

        click.echo(
            f"Downloading file from pod {pod_id}:{remote_path} to {absolute_local_path}..."
        )
        with ssh_cmd.SSHConnection(pod_id) as ssh:
            ssh.get_file(remote_path, absolute_local_path)
        click.echo(f"File downloaded successfully to {absolute_local_path}.")

    except Exception as e:
        click.echo(f"Failed to download file: {e}", err=True)
        click.echo(f"Ensure that the remote path exists on pod {pod_id}. \nAnd that the arguments are correct. \nFor example: runpod pod download 1234 /home/REMOTE_POD_PATH/file.txt /home/LOCAL_PATH/file.txt")