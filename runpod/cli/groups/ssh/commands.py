"""
Runpod | CLI | SSH | Commands
"""

import click
from prettytable import PrettyTable

from .functions import generate_ssh_key_pair, get_user_pub_keys


class SSHGroup(click.Group):
    """dispatches unknown first arguments to connect.

    `rp ssh <pod_id>` opens a terminal on the pod, exactly like plain
    `ssh <host>`; named subcommands (add, list, connect) still resolve
    normally.
    """

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            return "connect", self.commands["connect"], args


@click.group(
    "ssh",
    cls=SSHGroup,
    help="SSH into pods and manage the keys they trust.",
    invoke_without_command=False,
)
def ssh_cli():
    """SSH into pods and manage account SSH keys."""


@ssh_cli.command("connect")
@click.argument("pod_id")
def connect(pod_id):
    """Open an interactive terminal on a pod (also: rp ssh POD_ID)."""
    from runpod.cli.utils import ssh_cmd

    click.echo(f"Connecting to pod {pod_id}...")
    ssh = ssh_cmd.SSHConnection(pod_id)
    ssh.launch_terminal()


@ssh_cli.command("list")
def list_keys():
    """
    Lists the SSH keys for the current user.
    """
    key_list = get_user_pub_keys()
    table = PrettyTable(["Public Key", "Type", "Fingerprint"])
    for key in key_list:
        table.add_row((key["name"], key["type"], key["fingerprint"]))
    click.echo(table)


@ssh_cli.command("add")
@click.option("--key", default=None, help="The public key to add.")
@click.option(
    "--key-file", default=None, help="The file containing the public key to add."
)
def add_key(key, key_file):
    """
    Adds an SSH key to the current user account.
    If no key is provided, one will be generated.
    """
    if not key and not key_file:
        click.confirm("Would you like to add an SSH key to your account?", abort=True)
        key_name = click.prompt(
            "Please enter a name for this key", default="RunPod-Key", type=str
        )
        key_name = key_name.replace(" ", "-")
        generate_ssh_key_pair(key_name)

    click.echo("The key has been added to your account.")
