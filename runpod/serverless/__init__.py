''' Allows serverless to recognized as a package.'''

from . import pod_worker


def start():
    '''
    Starts the serverless worker.
    '''
    pod_worker.start_worker()
