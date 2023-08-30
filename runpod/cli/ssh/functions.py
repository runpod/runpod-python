'''
RunPod | CLI | SSH | Functions
'''

import os
import base64
import hashlib
import paramiko
import tomli as toml

from runpod.api.ctl_commands import get_user, update_user_settings

CONFIG_FILE = os.path.expanduser('~/.runpod/config.toml')

def get_ssh_key_fingerprint(public_key):
    '''
    Get the fingerprint of an SSH key
    '''
    parts = public_key.split()
    if len(parts) < 2:
        raise ValueError("Invalid SSH public key")

    key_data = base64.b64decode(parts[1])

    fingerprint = hashlib.sha256(key_data).digest()
    return "SHA256:" + base64.b64encode(fingerprint).decode('utf-8').strip('=')


def get_user_pub_keys():
    '''
    Get the current user's SSH keys
    '''
    user = get_user()
    keys = user['pubKey']

    # Parse the keys
    keys = keys.split('\n')
    keys = [key for key in keys if key != '']

    key_list = []
    for key in keys:
        key_parts = key.split(' ')

        # Basic validation
        if len(key_parts) < 2:
            continue

        key_dict = {}
        key_dict['type'] = key_parts[0]
        key_dict['key'] = key_parts[1]
        key_dict['fingerprint'] = get_ssh_key_fingerprint(key)
        key_dict['name'] = key_parts[2] if len(key_parts) > 2 else "N/A"
        key_list.append(key_dict)

    return key_list

def generate_ssh_key_pair(profile, filename):
    """
    Generate an RSA SSH key pair and save it to disk.

    Args:
    - filename (str): The base filename to save the key pair. The public key will have '.pub' appended to it.
    """
    # Generate private key
    private_key = paramiko.RSAKey.generate(bits=2048)
    private_key.write_private_key_file(filename)

    # Generate public key
    with open(f"{filename}.pub", "w", encoding="UTF-8") as public_file:
        public_key = f"{private_key.get_name()} {private_key.get_base64()}"
        public_file.write(public_key)

    add_ssh_key(public_key)

    # Add to config file
    with open(CONFIG_FILE, 'rb') as config_file:
        config = toml.load(config_file)

    with open(CONFIG_FILE, 'w', encoding="UTF-8") as config_file:
        config_file.write('[' + profile + ']\n')
        config_file.write('api_key = "' + config[profile]['api_key'] + '"\n')
        config_file.write('ssh_key = "' + filename + '"\n')

    return private_key, public_key


def add_ssh_key(public_key):
    '''
    Add an SSH key to the current user's account
    '''
    user = get_user()
    keys = user['pubKey']

    # Parse the keys
    keys = keys.split('\n')
    keys = [key for key in keys if key != '']

    # Check if the key already exists
    for key in keys:
        if public_key in key:
            return

    # Add the key
    keys.append(public_key)
    keys = '\n'.join(keys)

    # Update the user's keys
    update_user_settings(pubkey=keys)
