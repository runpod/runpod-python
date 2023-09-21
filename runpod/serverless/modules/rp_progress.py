"""
RunPod Progress Module
"""

import os
import asyncio
import threading
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

def _async_progress_update(job, progress):
    """
    The actual asynchronous function that sends the update.
    """
    session = _create_session()
    job_data = {
        "status": "IN_PROGRESS",
        "output": progress
    }

    # Setting up a new event loop for the thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(send_result(session, job_data, job))
    loop.close()

def progress_update(job, progress):
    """
    Updates the progress of a currently running job in a separate thread.
    """
    thread = threading.Thread(target=_async_progress_update, args=(job, progress), daemon=True)
    thread.start()
