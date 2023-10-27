'''
Commands for the config command group
'''
import sys
import click

from .functions import set_credentials, check_credentials

@click.command('config')
@click.argument('api-key', required=False, default=None)
@click.option('--profile', default='default', help='The profile to set the credentials for.')
def config_wizard(api_key, profile):
    '''
    Starts the config wizard.
    Should check if credentials are already set and prompt the user to overwrite them.
    '''
    valid, _ = check_credentials(profile)
    if valid:
        click.confirm(f'Credentials already set for profile: {profile}. Overwrite?', abort=True)

    if api_key is None:
        api_key = click.prompt('API Key', hide_input=False, confirmation_prompt=False)

    set_credentials(api_key, profile, overwrite=True)
    click.echo(f'Credentials set for profile: {profile} in ~/.runpod/config.toml')


# ------------------------------- Store API Key ------------------------------ #
@click.command('store_api_key')
@click.option('--profile', default='default', help='The profile to set the credentials for.')
@click.argument('api_key')
def store_api_key(profile, api_key):
    """Sets the credentials for a profile.
    Kept for backwards compatibility.
    """
    try:
        set_credentials(api_key, profile)
    except ValueError as err:
        click.echo(err)
        sys.exit(1)

    click.echo('Credentials set for profile: ' + profile + ' in ~/.runpod/config.toml')


# ------------------------- Validate Credentials File ------------------------ #
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
