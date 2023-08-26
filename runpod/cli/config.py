'''
runpod | cli | config.py

A collection of functions to set and validate configurations.
Configurations are TOML files located under ~/.runpod/
'''
import os

import tomli as toml

CREDENTIAL_FILE = os.path.expanduser('~/.runpod/credentials.toml')


def set_credentials(api_key: str, profile:str="default") -> None:
    '''
    Sets the user's credentials in ~/.runpod/credentials.toml
    If profile already exists user must use `update_credentials` instead.

    Args:
        api_key (str): The user's API key.
        profile (str): The profile to set the credentials for.

    --- File Structure ---

    [default]
    api_key = "RUNPOD_API_KEY"
    '''
    os.makedirs(os.path.dirname(CREDENTIAL_FILE), exist_ok=True)
    with open(CREDENTIAL_FILE, 'w+', encoding="UTF-8") as cred_file:
        credentials = toml.load(cred_file)
        if profile in credentials:
            raise ValueError('Profile already exists. Use `update_credentials` instead.')

        cred_file.write('[' + profile + ']\n')
        cred_file.write('api_key = "' + api_key + '"\n')


def check_credentials():
    '''
    Checks if the credentials file exists and is valid.
    '''
    if not os.path.exists(CREDENTIAL_FILE):
        return False, 'Error: ~/.runpod/credentials.toml does not exist.'

    # Check for default api_key
    try:
        config = toml.load(CREDENTIAL_FILE)

        if 'default' not in config:
            return False, 'Error: ~/.runpod/credentials.toml is missing default section.'

        if 'api_key' not in config['default']:
            return False, 'Error: ~/.runpod/credentials.toml is missing api_key.'

    except (TypeError, ValueError):
        return False, 'Error: ~/.runpod/credentials.toml is not a valid TOML file.'

    return True, None


def get_credentials(profile='default'):
    '''
    Returns the credentials for the specified profile from ~/.runpod/credentials.toml
    '''
    if not os.path.exists(CREDENTIAL_FILE):
        return None

    with open(CREDENTIAL_FILE, 'r', encoding="UTF-8") as cred_file:
        credentials = toml.load(cred_file)

    if profile not in credentials:
        return None

    return credentials[profile]
