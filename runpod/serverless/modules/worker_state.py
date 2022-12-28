import os
import uuid

current_job_id = None

worker_id = os.environ.get('RUNPOD_POD_ID', str(uuid.uuid4()))

auth_header = {"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"}
job_get_url = str(os.environ.get('RUNPOD_WEBHOOK_GET_JOB')).replace(
    '$ID', worker_id)
job_done_url = str(os.environ.get(
    'RUNPOD_WEBHOOK_POST_OUTPUT'))
job_done_url = job_done_url.replace(
    '$RUNPOD_POD_ID', worker_id)

webhook_ping = os.environ.get('RUNPOD_WEBHOOK_PING', None)
ping_interval = int(os.environ.get('RUNPOD_PING_INTERVAL', 10000))
ping_url = webhook_ping.replace(
    '$RUNPOD_POD_ID', worker_id)


def get_current_job_id():
    return current_job_id


def get_done_url():
    return job_done_url.replace('$ID', current_job_id)


def set_job_id(new_job_id):
    global current_job_id
    current_job_id = new_job_id
