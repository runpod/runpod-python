"""Helper functions for project group commands."""

from runpod import get_pods

def get_project_pod(project_id: str):
    """Check if a project pod exists.
    Return the pod_id if it exists, else return None.
    """
    for pod in get_pods():
        if project_id in pod['name']:
            return pod['id']

    return None
