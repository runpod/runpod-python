"""Enables heartbeats."""
# pylint: disable=too-few-public-methods

import os
import time
import threading

import requests

import runpod.serverless.modules.logging as log
from .worker_state import get_current_job_id, PING_URL, PING_INTERVAL

_session = requests.Session()
_session.headers.update({"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"})


class HeartbeatSender:
    ''' Sends heartbeats to the Runpod server. '''

    def __init__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start_ping(self):
        '''
        Starts the heartbeat thread.
        '''
        self._thread.start()

    def _run(self):
        '''
        Sends heartbeats to the Runpod server.
        '''
        while True:
            self._send_ping()
            time.sleep(int(PING_INTERVAL / 1000))

    def _send_ping(self):
        '''
        Sends a heartbeat to the Runpod server.
        '''
        job_id = get_current_job_id()

        ping_params = {
            'job_id': job_id,
        } if job_id is not None else None

        if PING_URL not in [None, 'PING_URL_NOT_SET']:
            try:
                result = _session.get(
                    PING_URL,
                    params=ping_params,
                    timeout=int(PING_INTERVAL / 1000)
                )

                log.debug(f"Heartbeat Sent | URL: {PING_URL} | Status: {result.status_code}")
                log.debug(f"Heartbeat | Interval: {PING_INTERVAL}ms | Params: {ping_params}")

            except Exception as err:  # pylint: disable=broad-except
                log.error(f"Heartbeat Failed  URL: {PING_URL}  Params: {ping_params}")
                log.error(f"Heartbeat Fail  Error: {err}")
