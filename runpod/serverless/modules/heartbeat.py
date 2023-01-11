'''enables heartbeats'''

import time
import os

import requests

import runpod.serverless.modules.logging as log
from .worker_state import get_current_job_id, PING_URL, ping_interval


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

        if PING_URL not in [None, 'PING_URL_NOT_SET']:
            result = session.get(
                PING_URL,
                params=ping_params,
                timeout=int(ping_interval/1000)
            )

            log.info(result)

        log.info(f'Heartbeat URL: {PING_URL} Interval: {ping_interval}ms')
        log.info(f"Heartbeat Params: {ping_params}")

    except Exception as err:  # pylint: disable=broad-except
        log.error(err)


def start_heartbeat():
    '''
    manages heartbeat timing
    '''
    session = requests.Session()
    session.headers.update({"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"})

    while True:
        heartbeat_ping(session)
        time.sleep(ping_interval/1000)
