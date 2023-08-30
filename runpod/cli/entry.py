'''
RunPod | CLI | Entry

The entry point for the CLI.
'''
import click

@click.group()
def runpod_cli():
    '''A collection of CLI functions for RunPod.'''

@runpod_cli.group('ssh')
def ssh_cli():
    '''A collection of CLI functions for SSH.'''
