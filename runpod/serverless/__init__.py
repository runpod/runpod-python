''' Allows serverless to recognized as a package.'''

import asyncio

from . import pod_worker


def start(config):
    '''
    Starts the serverless worker.
    '''
    asyncio.run(pod_worker.start_worker(config))
