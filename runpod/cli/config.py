'''
runpod | cli | config.py

A collection of functions to set and validate configurations.
Configurations are TOML files located under ~/.runpod/
'''

import os

import tomli as toml

CREDENTIAL_FILE = os.path.expanduser('~/.runpod/credentials.toml')

def set_credentials(api_key: str) -> None:
    '''
    Sets the user's credentials in ~/.runpod/credentials.toml

    Args:
        api_key (str): The user's API key.

    File Structure:

    [default]
    api_key = "RUNPOD_API_KEY"
    '''
    with open(CREDENTIAL_FILE, 'w', encoding="UTF-8") as cred_file:
        cred_file.write('[default]\n')
        cred_file.write('api_key = "' + api_key + '"\n')


def check_credentials():
    '''
    Checks if the credentials file exists and is valid.
    '''
    if not os.path.exists(CREDENTIAL_FILE):
        return False

    # Check for default api_key
    try:
        config = toml.load(CREDENTIAL_FILE)

        if 'default' not in config:
            return False

        if 'api_key' not in config['default']:
            return False

    except (TypeError, ValueError):
        print('Error: ~/.runpod/credentials.toml is not a valid TOML file.')

        return False

    return True
