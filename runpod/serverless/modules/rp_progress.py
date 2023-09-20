"""
Provides a method to update the progress of a currently running job.
"""

import os
import aiohttp
from .rp_http import send_result


def _create_session():
    """
    Creates an aiohttp session.
    """
    auth_header = {"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"}
    timeout = aiohttp.ClientTimeout(total=300, connect=2, sock_connect=2)

    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=None),
        headers=auth_header, timeout=timeout
    )

def progress_update(job, progress):
    """
    Updates the progress of a currently running job.
    """
    session = _create_session()

    job_data = {
        "status": "IN_PROGRESS",
        "output": progress
    }

    send_result(session, job_data, job)
