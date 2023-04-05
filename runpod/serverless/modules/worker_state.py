'''
Handles getting stuff from environment variables and updating the global state like job id.
'''

import os
import uuid

CURRENT_JOB_ID = None

WORKER_ID = os.environ.get('RUNPOD_POD_ID', str(uuid.uuid4()))


def get_auth_header():
    '''
    Returns the authorization header with the API key.
    '''
    return {"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"}


JOB_GET_URL = str(os.environ.get('RUNPOD_WEBHOOK_GET_JOB')).replace('$ID', WORKER_ID)
JOB_DONE_URL_TEMPLATE = str(os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT'))
JOB_DONE_URL_TEMPLATE = JOB_DONE_URL_TEMPLATE.replace('$RUNPOD_POD_ID', WORKER_ID)

WEBHOOK_PING = os.environ.get('RUNPOD_WEBHOOK_PING', None)
if WEBHOOK_PING is not None:
    PING_URL = WEBHOOK_PING.replace('$RUNPOD_POD_ID', WORKER_ID)
else:
    PING_URL = "PING_URL_NOT_SET"

PING_INTERVAL = int(os.environ.get('RUNPOD_PING_INTERVAL', 10000))


def get_current_job_id():
    '''
    Returns the current job id.
    '''
    return CURRENT_JOB_ID


def get_done_url():
    '''
    Constructs the done URL using the current job id.
    '''
    return JOB_DONE_URL_TEMPLATE.replace('$ID', CURRENT_JOB_ID)


def set_job_id(new_job_id):
    '''
    Sets the current job id.
    '''
    global CURRENT_JOB_ID  # pylint: disable=global-statement
    CURRENT_JOB_ID = new_job_id
