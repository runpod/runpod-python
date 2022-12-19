'''
PodWorker | modules | lifecycle.py

Performs the following lifecycle operations:
- Shutting down the worker
'''
# pylint: disable=too-few-public-methods

import os
import uuid
import threading
import requests

from .logging import log


class LifecycleManager:
    ''' Lifecycle Manager '''

    def __init__(self):
        '''
        Initialize Lifecycle Manager
        '''
        self.worker_id = os.environ.get('RUNPOD_POD_ID', str(uuid.uuid4()))
        log(f'Worker ID: {self.worker_id}')

        self.job_id = None

    def heartbeat_ping(self):
        '''
        Pings the heartbeat endpoint
        '''
        webhook_ping = os.environ.get('RUNPOD_WEBHOOK_PING', None)
        ping_interval = int(os.environ.get('RUNPOD_PING_INTERVAL', 10000))

        if self.job_id is not None:
            ping_params = {
                'job_id': self.job_id,
            }
        else:
            ping_params = {}

        if webhook_ping is not None:
            webhook_ping = webhook_ping.replace('$RUNPOD_POD_ID', self.worker_id)
            requests.get(webhook_ping, params=ping_params, timeout=ping_interval/1000)

        log(f'Heartbeat sent to {webhook_ping} interval: {ping_interval}ms params: {ping_params}')

        threading.Timer(ping_interval/1000, self.heartbeat_ping).start()
