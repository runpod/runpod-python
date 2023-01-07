'''
Handles getting stuff from env and updating global state like job id
'''

import os
import uuid

CURRENT_JOB_ID = None

worker_id = os.environ.get('RUNPOD_POD_ID', str(uuid.uuid4()))

auth_header = {"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"}
JOB_GET_URL = str(os.environ.get('RUNPOD_WEBHOOK_GET_JOB')).replace('$ID', worker_id)
JOB_DONE_URL = str(os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT'))
JOB_DONE_URL = JOB_DONE_URL.replace('$RUNPOD_POD_ID', worker_id)

webhook_ping = os.environ.get('RUNPOD_WEBHOOK_PING', None)
if webhook_ping is not None:
    PING_URL = webhook_ping.replace('$RUNPOD_POD_ID', worker_id)
else:
    PING_URL = "PING_URL_NOT_SET"

ping_interval = int(os.environ.get('RUNPOD_PING_INTERVAL', 10000))


def get_current_job_id():
    '''
    get current job id
    '''
    return CURRENT_JOB_ID


def get_done_url():
    '''
    constructs done url using current job id
    '''
    return JOB_DONE_URL.replace('$ID', CURRENT_JOB_ID)


def set_job_id(new_job_id):
    '''
    sets current job id
    '''
    global CURRENT_JOB_ID  # pylint: disable=global-statement
    CURRENT_JOB_ID = new_job_id
