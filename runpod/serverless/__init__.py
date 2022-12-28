''' Allows serverless to recognized as a package.'''

import asyncio

from . import work_loop


def start(config):
    '''
    Starts the serverless worker.
    '''
    asyncio.run(work_loop.start_worker(config))
