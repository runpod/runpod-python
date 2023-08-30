'''
Commands for the config command group
'''
import sys
import click

from .functions import set_credentials, check_credentials

@click.command('config')
@click.option('--profile', default='default', help='The profile to set the credentials for.')
@click.option('--api-key', default=None, help='The user\'s API key.')
def config_wizard(profile, api_key):
    '''
    Starts the config wizard.
    '''
    if api_key is None:
        api_key = click.prompt('Enter your RunPod API key: ', type=str, hide_input=False)
    set_credentials(api_key, profile)
    click.echo('Credentials set for profile: ' + profile + ' in ~/.runpod/config.toml')


@click.command('store_api_key')
@click.argument('api_key')
@click.option('--profile', default='default', help='The profile to set the credentials for.')
def store_api_key(api_key, profile):
    '''
    Sets the credentials for a profile.
    Kept for backwards compatibility.
    '''
    try:
        set_credentials(api_key, profile)
    except ValueError as err:
        click.echo(err)
        sys.exit(1)

    click.echo('Credentials set for profile: ' + profile + ' in ~/.runpod/config.toml')

@click.command('check_creds')
@click.option('--profile', default='default', help='The profile to check the credentials for.')
def validate_credentials_file(profile='default'):
    '''
    Validates the credentials file.
    Kept for backwards compatibility.
    '''
    click.echo('Validating ~/.runpod/config.toml')
    valid, error = check_credentials(profile)

    if not valid:
        click.echo(error)
        sys.exit(1)

    click.echo('Credentials file is valid.')
