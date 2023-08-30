'''
RunPod | CLI | Entry

The entry point for the CLI.
'''
import click

from .config.commands import config_wizard, store_api_key, validate_credentials_file
from .ssh.commands import ssh_cli
from .pod.commands import pod_cli

@click.group()
def runpod_cli():
    '''A collection of CLI functions for RunPod.'''

runpod_cli.add_command(config_wizard)
runpod_cli.add_command(store_api_key)
runpod_cli.add_command(validate_credentials_file)

runpod_cli.add_command(ssh_cli)
runpod_cli.add_command(pod_cli)
