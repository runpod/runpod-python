"""
Runpod | CLI | Entry

The entry point for the CLI.
"""

import click

from .groups.pod.commands import pod_cli
from .groups.ssh.commands import ssh_cli


@click.group()
def runpod_cli():
    """A collection of CLI functions for Runpod."""


runpod_cli.add_command(ssh_cli)  # runpod ssh
runpod_cli.add_command(pod_cli)  # runpod pod
