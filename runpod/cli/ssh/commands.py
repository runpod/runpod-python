'''
RunPod | CLI | SSH | Commands
'''

import click
from prettytable import PrettyTable
from .functions import get_user_pub_keys

@click.group('ssh')
def ssh_cli():
    '''A collection of CLI functions for SSH.'''

@ssh_cli.command('list-keys')
def list_keys():
    '''
    Lists the SSH keys for the current user.
    '''
    key_list = get_user_pub_keys()

    table = PrettyTable(['Key', 'Type', 'Fingerprint'])

    for key in key_list:
        table.add_row((key['name'], key['type'], key['fingerprint']))

    click.echo(table)
