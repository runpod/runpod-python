'''
RunPod | CLI | Pod | Commands
'''
import click
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
