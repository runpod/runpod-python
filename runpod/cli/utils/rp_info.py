"""Runpod | CLI | Utils | rp_info

A collection of utility functions for retrieving information about pods.
"""

import time

from runpod import get_pod


def get_pod_ssh_ip_port(pod_id, timeout=300):
    """
    Returns the IP and port for SSH access to a pod.
    """
    start_time = time.time()
    pod_ip = None
    pod_port = None
    status = None

    while time.time() - start_time < timeout and (pod_ip is None or pod_port is None):
        pod = get_pod(pod_id) or {}
        status = pod.get("status")
        direct_ssh = (pod.get("ssh") or {}).get("direct")

        if status == "RUNNING" and direct_ssh:
            pod_ip = direct_ssh["host"]
            pod_port = int(direct_ssh["port"])
            break

        runtime = pod.get("runtime") or {}
        if status == "RUNNING":
            for port in runtime.get("ports", []):
                if port["private"] == 22:
                    pod_ip = port["ip"]
                    pod_port = int(port["public"])
                    break

        time.sleep(1)

    if status != "RUNNING":
        raise TimeoutError(
            f"Pod {pod_id} did not reach 'RUNNING' state within {timeout} seconds."
        )

    if pod_ip is None or pod_port is None:
        raise TimeoutError(
            f"Pod {pod_id} did not report runtime data within {timeout} seconds."
        )

    return pod_ip, pod_port
