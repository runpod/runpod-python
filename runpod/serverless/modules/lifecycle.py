'''
PodWorker | modules | lifecycle.py

Performs the following lifecycle operations:
- Shutting down the worker
'''

import os
import sys
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

        self.ttl = int(os.environ.get('TERMINATE_IDLE_TIME', 60000))
        if self.ttl == 0:
            self.is_worker_zero = True  # Special case for worker 0
        else:
            self.is_worker_zero = False

        self.work_in_progress = False   # Flag to check if worker is busy
        self.work_timeout = int(os.environ.get('EXECUTION_TIMEOUT', 300000))

        self.job_id = None

    def reset_worker_ttl(self):
        '''
        Resets the TTL of the worker
        '''
        self.ttl = int(os.environ.get('TERMINATE_IDLE_TIME', 60000))
        self.work_timeout = int(os.environ.get('EXECUTION_TIMEOUT', 300000))
        self.work_in_progress = False
        log(f'Worker TTL extended. TTL: {self.ttl}')

        self.job_id = None

    def seppuku(self):
        '''
        Kill the worker
        '''
        if len(self.worker_id) < 20:
            graphql_url = f"https://api.runpod.io/graphql?api_key={os.environ.get('RUNPOD_API_KEY')}"   # pylint: disable=line-too-long
            graphql_query = f'''
                            mutation {{
                                podTerminate(
                                    input: {{
                                        podId: "{self.worker_id}"
                                    }}
                                )
                            }}
                            '''
            requests.post(graphql_url, json={
                          'query': graphql_query}, timeout=30)

        log(f'Worker {self.worker_id} is terminating itself')
        sys.exit(0)

    def check_worker_ttl_thread(self):
        '''
        Check worker TTL.
        Reduce TTL or timeout by 1 every second.
        '''
        if os.environ.get('TEST_LOCAL', 'false') != 'true':
            if self.ttl <= 0 or self.work_timeout <= 0:
                self.seppuku()

            if not self.work_in_progress:
                self.ttl -= 1
            else:
                self.work_timeout -= 1000

            threading.Timer(1, self.check_worker_ttl_thread).start()

    def heartbeat_ping(self):
        '''
        Pings the heartbeat endpoint
        '''
        webhook_ping = os.environ.get('WEBHOOK_PING', None)
        ping_interval = int(os.environ.get('PING_INTERVAL', 10000))

        ping_params = {
            'job_id': self.job_id,
        }

        if webhook_ping is not None:
            webhook_ping = webhook_ping.replace('$RUNPOD_POD_ID', self.worker_id)
            requests.get(webhook_ping, params=ping_params, timeout=ping_interval/1000)

        threading.Timer(ping_interval/1000, self.heartbeat_ping).start()
