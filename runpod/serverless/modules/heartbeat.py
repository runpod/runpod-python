'''enables heartbeats'''

import time
import os

import requests

import runpod.serverless.modules.logging as log
from .worker_state import get_current_job_id, ping_url, ping_interval

# COUNTER = 0


def heartbeat_ping(session):
    '''
    Pings the heartbeat endpoint
    '''
    ping_params = None

    try:
        job_id = get_current_job_id()

        if job_id is not None:
            ping_params = {
                'job_id': job_id,
            }

            session.get(
                ping_url,
                params=ping_params,
                timeout=int(ping_interval/1000)
            )

        log.info(
            f'Heartbeat sent to {ping_url} interval: {ping_interval}ms params: {ping_params}')
    except Exception as err:  # pylint: disable=broad-except
        log.error(err)


def start_heartbeat():
    '''
    manages heartbeat timing
    '''

    session = requests.Session()
    session.headers.update({
        "Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"})
    while True:
        heartbeat_ping(session)
        time.sleep(ping_interval/1000)
