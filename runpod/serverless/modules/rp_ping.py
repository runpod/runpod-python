"""
This module defines the Heartbeat class.
The heartbeat is responsible for sending periodic pings to the Runpod server.
"""
import os
import time
import threading

import requests

from runpod.serverless.modules.rp_logger import RunPodLogger
from .worker_state import Jobs, WORKER_ID

log = RunPodLogger()
jobs = Jobs() # Contains the list of jobs that are currently running.


class Heartbeat:
    ''' Sends heartbeats to the Runpod server. '''

    PING_URL = os.environ.get('RUNPOD_WEBHOOK_PING', "PING_NOT_SET")
    PING_URL = PING_URL.replace('$RUNPOD_POD_ID', WORKER_ID)
    PING_INTERVAL = int(os.environ.get('RUNPOD_PING_INTERVAL', 10000))//1000

    _session = requests.Session()
    _session.headers.update({"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"})

    def start_ping(self, test=False):
        '''
        Sends heartbeat pings to the Runpod server.
        '''
        if self.PING_URL in ["PING_NOT_SET", None]:
            log.error("Ping URL not set, cannot start ping.")
            return

        threading.Thread(target=self.ping_loop, daemon=True, args=(test,)).start()

    def ping_loop(self, test=False):
        '''
        Sends heartbeat pings to the Runpod server.
        '''
        while True:
            try:
                self._send_ping()
                time.sleep(self.PING_INTERVAL)
            except requests.RequestException as err:
                log.error(f"Ping Error: {err}, attempting to restart ping.")
                if test:
                    return

            if test:
                return

    def _send_ping(self):
        '''
        Sends a heartbeat to the Runpod server.
        '''
        job_ids = jobs.get_job_list()
        ping_params = {'job_id': job_ids} if job_ids is not None else None

        result = self._session.get(
            self.PING_URL, params=ping_params,
            timeout=self.PING_INTERVAL
        )

        log.debug(f"Heartbeat Sent | URL: {self.PING_URL} | Status: {result.status_code}")
