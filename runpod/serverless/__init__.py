''' Allows serverless to recognized as a package.'''

from . import pod_worker


def start(config):
    '''
    Starts the serverless worker.
    '''
    pod_worker.start_worker(config)
