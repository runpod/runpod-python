"""RunPod | CLI | Utils | rp_info

A collection of utility functions for retrieving information about pods.
"""

import time

from runpod import get_pod

def get_pod_ssh_ip_port(pod_id, timeout=300):
    '''
    Returns the IP and port for SSH access to a pod.
    '''
    start_time = time.time()
    pod_ip = None
    pod_port = None

    while time.time() - start_time < timeout and (pod_ip is None or pod_port is None):
        pod = get_pod(pod_id)
        desired_status = pod.get('desiredStatus', None)
        runtime = pod.get('runtime', None)

        if desired_status == 'RUNNING' and runtime and 'ports' in pod['runtime']:
            for port in pod['runtime']['ports']:
                if port['privatePort'] == 22:
                    pod_ip = port['ip']
                    pod_port = int(port['publicPort'])
                    break

        time.sleep(1)

    if desired_status != 'RUNNING':
        raise TimeoutError(f"Pod {pod_id} did not reach 'RUNNING' state within {timeout} seconds.")

    if runtime is None:
        raise TimeoutError(f"Pod {pod_id} did not report runtime data within {timeout} seconds.")

    return pod_ip, pod_port
