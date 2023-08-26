'''
RunPod | CLI Commands

A collection of CLI functions.
'''
import click

from .config import check_credentials

@click.group()
def runpod_cli():
    '''
    A collection of CLI functions.
    '''
    pass


@runpod_cli.command('check_creds')
def validate_credentials_file():
    '''
    Validates the credentials file.
    '''
    valid, error = check_credentials()
    if not valid:
        click.echo(error)
        exit(1)

    click.echo('Credentials file is valid.')
