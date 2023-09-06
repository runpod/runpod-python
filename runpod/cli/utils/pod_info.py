'''
RunPod | CLI | Utils | Pod Info
'''

import time

from runpod import get_pod

def get_ssh_ip_port(pod_id, timeout=300):
    '''
    Returns the IP and port for SSH access to a pod.
    '''
    start_time = time.time()
    while time.time() - start_time < timeout:
        pod = get_pod(pod_id)
        desired_status = pod.get('desiredStatus', None)
        runtime = pod.get('runtime', None)

        if desired_status == 'RUNNING' and runtime:
            for port in pod['runtime']['ports']:
                if port['privatePort'] == 22:
                    return port['ip'], port['publicPort']

        time.sleep(1)

    if desired_status != 'RUNNING':
        raise TimeoutError(f"Pod {pod_id} did not reach 'RUNNING' state within {timeout} seconds.")

    if runtime:
        raise TimeoutError(f"Pod {pod_id} did not report runtime data within {timeout} seconds.")

    return None, None
