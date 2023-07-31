"""Enables heartbeats."""
# pylint: disable=too-few-public-methods

import os
import asyncio

import aiohttp

from runpod.serverless.modules.rp_logger import RunPodLogger
from .worker_state import Jobs, WORKER_ID

# --------------------------------- Variables -------------------------------- #
PING_URL = os.environ.get('RUNPOD_WEBHOOK_PING', "PING_NOT_SET")
PING_URL = PING_URL.replace('$RUNPOD_POD_ID', WORKER_ID)
PING_INTERVAL = int(os.environ.get('RUNPOD_PING_INTERVAL', 10000))

log = RunPodLogger()
jobs = Jobs() # Contains the list of jobs that are currently running.


class HeartbeatSender:
    ''' Sends heartbeats to the Runpod server. '''

    _instance = None

    def __new__(cls):
        if HeartbeatSender._instance is None:
            HeartbeatSender._instance = object.__new__(cls)
        return HeartbeatSender._instance

    async def start_ping(self, test=False):
        '''
        Sends heartbeat pings to the Runpod server.
        '''
        while True:
            try:
                while True:
                    await self._send_ping()
                    await asyncio.sleep(int(PING_INTERVAL / 1000))

                    if test:
                        return
            except (aiohttp.ClientError, asyncio.TimeoutError, asyncio.CancelledError) as err:
                log.error(f"Ping Error: {err}, attempting to restart ping.")
                if test:
                    return


    async def _send_ping(self):
        '''
        Sends a heartbeat to the Runpod server.
        '''
        async with aiohttp.ClientSession(
            headers={"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"} ) as session:
            job_ids = jobs.get_job_list()

            ping_params = {
                'job_id': job_ids,
            } if job_ids is not None else None

            if PING_URL not in [None, 'PING_NOT_SET']:
                result = await session.get(
                    PING_URL,
                    params=ping_params,
                    timeout=int(PING_INTERVAL / 1000)
                )

                log.debug(f"Heartbeat Sent | URL: {PING_URL} | Status: {result.status}")
