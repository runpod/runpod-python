"""
Commands for the config command group
"""

import sys

import click

from .functions import check_credentials, set_credentials


@click.command("config", help="Configures the Runpod CLI with the user's API key.")
@click.argument("api-key", required=False, default=None)
@click.option(
    "--profile", default="default", help="The profile to set the credentials for."
)
@click.option("--check", is_flag=True, help="Check if credentials are already set.")
def config_wizard(api_key, profile, check):
    """Starts the configuration wizard to set up the Runpod CLI.
    If credentials are already set, prompts the user to overwrite them.
    """
    valid, error = check_credentials(profile)

    if check and valid:
        click.echo("Credentials already set for profile: " + profile)
        sys.exit(0)
    elif check and not valid:
        click.echo(error)
        sys.exit(1)

    if valid:
        click.confirm(
            f"Credentials already set for profile: {profile}. Overwrite?", abort=True
        )

    if api_key is None:
        click.echo("Please enter your Runpod API Key.")
        click.echo("You can find it at https://console.runpod.io/user/settings")
        api_key = click.prompt(
            "    > Runpod API Key", hide_input=False, confirmation_prompt=False
        )

    set_credentials(api_key, profile, overwrite=True)

    click.echo(f"Credentials set for profile: {profile} in ~/.runpod/config.toml")
