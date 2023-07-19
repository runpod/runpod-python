'''
runpod | cli | config.py

A collection of functions to set and validate configurations.
Configurations are TOML files located under ~/.runpod/
'''

import os
import tomllib

CREDENTIAL_FILE = os.path.expanduser('~/.runpod/credentials.toml')

def set_credentials(api_key):
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
    if not os.path.exists(os.path.expanduser('~/.runpod')):
        return False

    if not os.path.exists(CREDENTIAL_FILE):
        return False

    # Check for default api_key
    try:
        config = tomllib.load(CREDENTIAL_FILE)

        if 'default' not in config:
            return False

        if 'api_key' not in config['default']:
            return False

    except tomllib.TOMLDecodeError:
        print('Error: ~/.runpod/credentials.toml is not a valid TOML file.')

        return False

    return True
