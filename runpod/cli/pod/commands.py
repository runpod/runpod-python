'''
RunPod | CLI | Pod | Commands
'''

import click
from prettytable import PrettyTable

from runpod import get_pods, create_pod

@click.group('pod')
def pod_cli():
    '''A collection of CLI functions for Pod.'''

@pod_cli.command('list')
def list_pods():
    '''
    Lists the pods for the current user.
    '''
    pod_list = get_pods()

    table = PrettyTable(['ID', 'Name', 'Status', 'Image'])
    for pod in pod_list:
        table.add_row((pod['id'], pod['name'], pod['desiredStatus'], pod['imageName']))

    click.echo(table)

@pod_cli.command('create')
@click.option('--name', default=None, help='The name of the pod.')
@click.option('--image', default=None, help='The image to use for the pod.')
@click.option('--gpu-type', default=None, help='The GPU type to use for the pod.')
@click.option('--gpu-count', default=None, help='The number of GPUs to use for the pod.')
@click.option('--support-public-ip', default=False, help='Whether or not to support a public IP.')
@click.option('--template-file', default=None, help='The template file to use for the pod.')
def create_new_pod(name, image, gpu_type, gpu_count, support_public_ip, template_file):
    '''
    Creates a pod.
    '''
    quick_launch = click.confirm('Would you like to launch default pod?', abort=True)
    if quick_launch:
        name = 'RunPod-Default-Pod'
        image = 'runpod/pytorch:2.0.1-py3.10-cuda11.8.0-devel'
        gpu_type = 'NVIDIA GeForce RTX 3090'
        support_public_ip = True
        ports ='22/tcp'

        click.echo('Launching default pod...')

    new_pod = create_pod(name, image, gpu_type, support_public_ip=support_public_ip, ports=ports)

    click.echo(f'Pod {new_pod["id"]} has been created.')

@pod_cli.command('connect')
@click.argument('pod_id')
def connect_to_pod(pod_id):
    '''
    Connects to a pod.
    '''
    click.echo(f'Connecting to pod {pod_id}...')
