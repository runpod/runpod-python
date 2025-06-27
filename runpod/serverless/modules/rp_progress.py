"""
Runpod Progress Module
"""

import asyncio
import threading
from typing import Any, Dict

from runpod.http_client import AsyncClientSession
from runpod.serverless.modules.rp_logger import RunPodLogger

from .rp_http import send_result

log = RunPodLogger()


async def _async_progress_update(session, job, progress):
    """
    The actual asynchronous function that sends the update.
    """
    job_data = {"status": "IN_PROGRESS", "output": progress}

    await send_result(session, job_data, job)


def _thread_target(job: Dict[str, Any], progress: Any):
    """
    A wrapper around _async_progress_update to handle the event loop.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:

        async def main():
            session = AsyncClientSession()
            async with session:
                await _async_progress_update(session, job, progress)

        loop.run_until_complete(main())

        log.debug(f'{job["id"]} | Progress Update Sent: {progress}')
    finally:
        loop.close()


def progress_update(job: Dict[str, Any], progress: Any) -> None:
    """
    Updates the progress of a currently running job in a separate thread.
    """
    log.debug(f'{job["id"]} | Sending Progress Update: {progress}')
    thread = threading.Thread(target=_thread_target, args=(job, progress), daemon=True)
    thread.start()
