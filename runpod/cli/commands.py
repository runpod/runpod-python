'''
RunPod | CLI Commands

A collection of CLI functions.
'''
import sys
import click

from .config import set_credentials, check_credentials

@click.group()
def runpod_cli():
    '''A collection of CLI functions for RunPod.'''

@runpod_cli.command('store_api_key')
@click.argument('api_key')
@click.option('--profile', default='default', help='The profile to set the credentials for.')
def store_api_key(api_key, profile):
    '''
    Sets the credentials for a profile.
    '''
    try:
        set_credentials(api_key, profile)
    except ValueError as err:
        click.echo(err)
        sys.exit(1)

    click.echo('Credentials set for profile: ' + profile + ' in ~/.runpod/credentials.toml')


@runpod_cli.command('check_creds')
@click.option('--profile', default='default', help='The profile to check the credentials for.')
def validate_credentials_file(profile='default'):
    '''
    Validates the credentials file.
    '''
    click.echo('Validating ~/.runpod/credentials.toml')
    valid, error = check_credentials(profile)

    if not valid:
        click.echo(error)
        sys.exit(1)

    click.echo('Credentials file is valid.')
