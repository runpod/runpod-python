'''
RunPod | CLI | Exec | Commands
'''

import click

from .functions import python_over_ssh
from .helpers import get_session_pod

@click.group('exec', help='Execute commands in a pods.')
def exec_cli():
    '''A collection of CLI functions for Exec.'''

@exec_cli.command('python')
@click.option('--pod_id', default=None, help='The pod ID to run the command on.')
@click.argument('file', type=click.Path(exists=True), required=True)
def remote_python(pod_id, file):
    '''
    Runs a remote Python shell.
    '''
    if pod_id is None:
        pod_id = get_session_pod()

    click.echo('Running remote Python shell...')
    python_over_ssh(pod_id, file)
