"""
RunPod Progress Module
"""

import os
import asyncio
import threading
import aiohttp

from .rp_http import send_result

async def _create_session_async():
    """
    Creates an aiohttp session.
    """
    auth_header = {"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"}
    timeout = aiohttp.ClientTimeout(total=300, connect=2, sock_connect=2)

    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=None),
        headers=auth_header, timeout=timeout
    )

async def _async_progress_update(session, job, progress):
    """
    The actual asynchronous function that sends the update.
    """
    job_data = {
        "status": "IN_PROGRESS",
        "output": progress
    }

    await send_result(session, job_data, job)

def _thread_target(job, progress):
    """
    A wrapper around _async_progress_update to handle the event loop.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    session = loop.run_until_complete(_create_session_async())
    loop.run_until_complete(_async_progress_update(session, job, progress))

    session.close()
    loop.close()

def progress_update(job, progress):
    """
    Updates the progress of a currently running job in a separate thread.
    """
    thread = threading.Thread(target=_thread_target, args=(job, progress), daemon=True)
    thread.start()
