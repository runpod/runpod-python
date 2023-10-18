"""Helper functions for project group commands."""

import re

import click

from runpod import get_pods

def validate_project_name(name):
    '''
    Validate the project name.
    '''
    match = re.search(r"[<>:\"/\\|?*\s]", name)
    if match:
        raise click.BadParameter(f"Project name contains an invalid character: '{match.group()}'.")
    return name

def get_project_pod(project_id: str):
    """Check if a project pod exists.
    Return the pod_id if it exists, else return None.
    """
    for pod in get_pods():
        if project_id in pod['name']:
            return pod['id']

    return None
