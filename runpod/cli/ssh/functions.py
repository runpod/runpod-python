'''
RunPod | CLI | SSH | Functions
'''
from runpod import get_user

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
        key_dict = {}
        key = key.split(' ')
        key_dict['key'] = key[2]
        key_dict['type'] = key[0]
        key_dict['fingerprint'] = key[1]
        key_list.append(key_dict)

    return key_list
