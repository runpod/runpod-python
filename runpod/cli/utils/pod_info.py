'''
RunPod | CLI | Utils | Pod Info
'''

def get_ssh_ip_port(pod):
    '''
    Returns the IP and port for SSH access to a pod.
    '''
    if pod['desiredStatus'] == 'RUNNING':
        for port in pod['runtime']['ports']:
            if port['privatePort'] == 22:
                pod_ip = port['ip']
                pod_port = port['publicPort']

    return pod_ip, pod_port
