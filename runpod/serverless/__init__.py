''' Allows serverless to recognized as a package.'''

import os
import asyncio

from . import work_loop
from .modules import rp_fastapi


def start(config):
    '''
    Starts the serverless worker.
    '''
    realtime_port = os.environ.get('RUNPOD_REALTIME_PORT', None)
    realtime_concurrency = os.environ.get('RUNPOD_REALTIME_CONCURRENCY', 1)

    if realtime_port:
        api_server = rp_fastapi.WorkerAPI()
        api_server.config = config

        api_server.start_uvicorn(realtime_port, realtime_concurrency)

    else:
        asyncio.run(work_loop.start_worker(config))
