'''
RunPod | CLI | Utils | Pod Info
'''

import time

def get_ssh_ip_port(pod):
    '''
    Returns the IP and port for SSH access to a pod.
    Tries up to 3 times with an incremental backoff if necessary.
    '''
    pod_ip = None
    pod_port = None

    for attempt in range(3):
        if pod['desiredStatus'] == 'RUNNING':
            ports = []  # Default to an empty list
            if pod.get('runtime'):
                ports = pod['runtime'].get('ports', [])

            for port in ports:
                if port['privatePort'] == 22:
                    pod_ip = port['ip']
                    pod_port = port['publicPort']
                    break  # Breaks out of the inner loop

            # If we have successfully fetched the IP and port, break out of the retry loop
            if pod_ip is not None and pod_port is not None:
                break

        # If we have not successfully fetched the IP and port, sleep before retrying
        if attempt < 2:  # We don't want to sleep after the third attempt
            time.sleep(2 * (attempt + 1))  # Sleeps for 2, 4 seconds

    if pod_ip is None or pod_port is None:
        raise Exception("Failed to retrieve SSH IP and port after 3 attempts.")

    return pod_ip, pod_port
