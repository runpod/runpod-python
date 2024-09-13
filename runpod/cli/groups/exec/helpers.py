"""Helper functions for the runpod cli group exec command"""

import os

import click

from runpod import get_pod

POD_ID_FILE = os.path.join(os.path.expanduser("~"), ".runpod", "pod_id")


def get_session_pod():
    """Returns the pod_id for the current session.

    Session pod is used to execute commands and run scripts remotely.

    - If the pod_id is already set, return it.
    - If the pod_id is not set, prompt the user for it.
    - Save the pod_id to a file so that the user doesn't have to enter it again.
    """
    pod_id = None

    if os.path.exists(POD_ID_FILE):
        with open(POD_ID_FILE, "r", encoding="UTF-8") as pod_file:
            pod_id = pod_file.read().strip()

    # Confirm that the pod_id is valid
    if get_pod(pod_id) is not None:
        return pod_id

    # If file doesn't exist or is empty, prompt user for the pod_id
    pod_id = click.prompt("Please provide the pod ID")
    os.makedirs(os.path.dirname(POD_ID_FILE), exist_ok=True)
    with open(POD_ID_FILE, "w", encoding="UTF-8") as pod_file:
        pod_file.write(pod_id)

    return pod_id
