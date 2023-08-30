'''
RunPod | CLI | Exec | Commands
'''

import click

from .functions import python_over_ssh

@click.group('exec')
def exec_cli():
    '''A collection of CLI functions for Exec.'''

@exec_cli.command('python')
@click.option('--pod_id', default=None, help='The pod ID to run the command on.')
@click.option('--file', default=None, help='The file to run.')
def remote_python(pod_id, file):
    '''
    Runs a remote Python shell.
    '''
    click.echo('Running remote Python shell...')
    python_over_ssh(pod_id, file)
