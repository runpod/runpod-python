'''
RunPod | CLI | Entry

The entry point for the CLI.
'''
import click

from runpod.cli.config.commands import config_wizard
from runpod.cli.ssh.commands import ssh_cli

@click.group()
def runpod_cli():
    '''A collection of CLI functions for RunPod.'''

runpod_cli.add_command(config_wizard)
runpod_cli.add_command(ssh_cli)
