'''enables heartbeats'''

import asyncio

import runpod.serverless.modules.logging as log
from .worker_state import get_current_job_id, ping_url, ping_interval


async def send_ping(session):
    '''
    sends the http request
    '''
    try:
        ping_params = None
        job_id = get_current_job_id()
        if job_id is not None:
            ping_params = {
                'job_id': job_id,
            }
        await session.get(ping_url, params=ping_params,
                          timeout=int(ping_interval/1000))
    except Exception as err:  # pylint: disable=broad-except
        log.warn(f"Error while sending heartbeat: {err}")


async def heartbeat_ping(session):
    '''
    manages heartbeat timing
    '''
    if ping_url is not None:
        while True:

            asyncio.create_task(
                send_ping(session)
            )

            await asyncio.sleep(ping_interval/1000)
