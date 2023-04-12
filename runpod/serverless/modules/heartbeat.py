"""Enables heartbeats."""

import os
import threading

import requests

import runpod.serverless.modules.logging as log
from .worker_state import get_current_job_id, PING_URL, PING_INTERVAL

_session = requests.Session()
_session.headers.update({"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"})


def _send_ping(ping_params=None):
    if PING_URL not in [None, 'PING_URL_NOT_SET']:
        try:
            result = _session.get(
                PING_URL,
                params=ping_params,
                timeout=int(PING_INTERVAL / 1000)
            )

            log.info(f"Heartbeat Sent  URL: {PING_URL}  Status: {result.status_code}")
            log.info(f"Heartbeat Sent  Interval: {PING_INTERVAL}ms  Params: {ping_params}")

        except Exception as err:  # pylint: disable=broad-except
            log.error(f"Heartbeat Failed  URL: {PING_URL}  Params: {ping_params}")
            log.error(f"Heartbeat Fail  Error: {err}")


def start_ping():
    """
    Pings the heartbeat endpoint at the specified interval.
    """
    job_id = get_current_job_id()

    ping_params = {
        'job_id': job_id,
    } if job_id is not None else None

    _send_ping(ping_params)

    log.debug(f"Scheduling next heartbeat in {PING_INTERVAL}ms")
    heartbeat_thread = threading.Timer(int(PING_INTERVAL / 1000), start_ping)
    heartbeat_thread.daemon = True
    heartbeat_thread.start()
