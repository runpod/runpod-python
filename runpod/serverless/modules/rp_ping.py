"""Enables heartbeats."""
# pylint: disable=too-few-public-methods

import os
import time
import threading

import requests

from runpod.serverless.modules.rp_logger import RunPodLogger
from .worker_state import Jobs, WORKER_ID

# --------------------------------- Variables -------------------------------- #
PING_URL = os.environ.get('RUNPOD_WEBHOOK_PING', "PING_NOT_SET")
PING_URL = PING_URL.replace('$RUNPOD_POD_ID', WORKER_ID)
PING_INTERVAL = int(os.environ.get('RUNPOD_PING_INTERVAL', 10000))

log = RunPodLogger()
jobs = Jobs() # Contains the list of jobs that are currently running.

_session = requests.Session()
_session.headers.update({"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"})


class HeartbeatSender:
    ''' Sends heartbeats to the Runpod server. '''

    _instance = None
    _thread = None

    def __new__(cls):
        if HeartbeatSender._instance is None:
            HeartbeatSender._instance = object.__new__(cls)
            HeartbeatSender._thread = threading.Thread(
                target=HeartbeatSender._instance._run, daemon=True)
        return HeartbeatSender._instance

    def start_ping(self):
        '''
        Starts the heartbeat thread.
        '''
        if not self._thread.is_alive():
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
        job_ids = jobs.get_job_list()

        ping_params = {
            'job_id': job_ids,
        } if job_ids is not None else None

        if PING_URL not in [None, 'PING_NOT_SET']:
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
