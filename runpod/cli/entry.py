'''
RunPod | CLI | Entry

The entry point for the CLI.
'''
import click

from .groups.project.commands import project_cli

from .groups.config.commands import config_wizard
from .groups.ssh.commands import ssh_cli
from .groups.pod.commands import pod_cli
from .groups.exec.commands import exec_cli

@click.group()
def runpod_cli():
    '''A collection of CLI functions for RunPod.'''

runpod_cli.add_command(config_wizard) # runpod config

runpod_cli.add_command(ssh_cli) # runpod ssh
runpod_cli.add_command(pod_cli) # runpod pod
runpod_cli.add_command(exec_cli) # runpod exec
runpod_cli.add_command(project_cli) # runpod project
