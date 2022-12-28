import asyncio

from .worker_state import get_current_job_id, ping_url, ping_interval


async def heartbeat_ping(session):

    ping_params = None

    if ping_url is not None:
        while True:
            job_id = get_current_job_id()

            if job_id is not None:
                ping_params = {
                    'job_id': job_id,
                }

            asyncio.create_task(
                session.get(ping_url, params=ping_params,
                            timeout=int(ping_interval/1000))
            )

            await asyncio.sleep(ping_interval/1000)
