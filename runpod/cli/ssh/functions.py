'''
RunPod | CLI | SSH | Functions
'''

import base64
import hashlib

from runpod.api.ctl_commands import get_user

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
