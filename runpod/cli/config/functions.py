'''
runpod | cli | config.py

A collection of functions to set and validate configurations.
Configurations are TOML files located under ~/.runpod/
'''
import os
from pathlib import Path

import tomli as toml

CREDENTIAL_FILE = os.path.expanduser('~/.runpod/config.toml')


def set_credentials(api_key: str, profile:str="default", overwrite=False) -> None:
    '''
    Sets the user's credentials in ~/.runpod/config.toml
    If profile already exists user must use `update_credentials` instead.

    Args:
        api_key (str): The user's API key.
        profile (str): The profile to set the credentials for.

    --- File Structure ---

    [default]
    api_key = "RUNPOD_API_KEY"
    '''
    os.makedirs(os.path.dirname(CREDENTIAL_FILE), exist_ok=True)
    Path(CREDENTIAL_FILE).touch(exist_ok=True)

    if not overwrite:
        with open(CREDENTIAL_FILE, 'rb') as cred_file:
            if profile in toml.load(cred_file):
                raise ValueError('Profile already exists. Use `update_credentials` instead.')

    with open(CREDENTIAL_FILE, 'w', encoding="UTF-8") as cred_file:
        cred_file.write('[' + profile + ']\n')
        cred_file.write('api_key = "' + api_key + '"\n')


def check_credentials(profile:str="default"):
    '''
    Checks if the credentials file exists and is valid.
    '''
    if not os.path.exists(CREDENTIAL_FILE):
        return False, '~/.runpod/config.toml does not exist.'

    # Check for default api_key
    try:
        with open(CREDENTIAL_FILE, 'rb') as cred_file:
            config = toml.load(cred_file)

        if profile not in config:
            return False, f'~/.runpod/config.toml is missing {profile} profile.'

        if 'api_key' not in config[profile]:
            return False, f'~/.runpod/config.toml is missing api_key for {profile} profile.'

    except (TypeError, ValueError):
        return False, '~/.runpod/config.toml is not a valid TOML file.'

    return True, None


def get_credentials(profile='default'):
    '''
    Returns the credentials for the specified profile from ~/.runpod/config.toml
    '''
    if not os.path.exists(CREDENTIAL_FILE):
        return None

    with open(CREDENTIAL_FILE, 'rb') as cred_file:
        credentials = toml.load(cred_file)

    if profile not in credentials:
        return None

    return credentials[profile]
