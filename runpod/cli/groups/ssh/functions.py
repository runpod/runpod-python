'''
RunPod | CLI | SSH | Functions
'''

import os
import base64
import hashlib
import paramiko

from runpod.api.ctl_commands import get_user, update_user_settings

SSH_FILES = os.path.expanduser('~/.runpod/ssh')


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


def generate_ssh_key_pair(filename):
    """
    Generate an RSA SSH key pair and save it to disk.

    Args:
    - filename (str):   The base filename to save the key pair.
                        The public key will have '.pub' appended to it.
    """
    os.makedirs(os.path.join(SSH_FILES), exist_ok=True)

    # Generate private key
    private_key = paramiko.RSAKey.generate(bits=2048)
    private_key.write_private_key_file(os.path.join(SSH_FILES, filename))

    # Set permissions
    os.chmod(os.path.join(SSH_FILES, filename), 0o600)

    # Generate public key
    with open(f"{SSH_FILES}/{filename}.pub", "w", encoding="UTF-8") as public_file:
        public_key = f"{private_key.get_name()} {private_key.get_base64()} {filename}"
        public_file.write(public_key)

    add_ssh_key(public_key)

    return private_key, public_key


def add_ssh_key(public_key):
    """Add an SSH public key to the current user's RunPod account.
    Checks if the key already exists before adding it.
    """
    user = get_user()
    current_keys = user['pubKey']

    # Check if the key already exists
    if public_key in current_keys:
        print("Key already exists")
        return

    updated_keys = current_keys + ('\n\n' if current_keys else '') + public_key

    # Update the user's keys
    update_user_settings(f"{updated_keys}")
