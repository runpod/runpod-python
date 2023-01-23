''' Allows serverless to recognized as a package.'''

import os
import asyncio

from . import work_loop
from .modules import rp_fastapi


def start(config):
    '''
    Starts the serverless worker.
    '''
    api_port = os.environ.get('RUNPOD_REALTIME_PORT', None)

    if api_port:
        api_server = rp_fastapi.WorkerAPI()
        api_server.config = config

        api_server.start_uvicorn(api_port)
    else:
        asyncio.run(work_loop.start_worker(config))
