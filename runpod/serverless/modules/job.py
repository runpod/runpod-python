''' PodWorker | jobs.py '''

import os
import time
import json
import requests

from .logging import log
from . import upload, inference


def get(worker_id):
    '''
    Get next job from job endpoint, returns job json.
    The job format is:
    {
        "id": {job_id},
        "input": {job_input}
    }
    '''
    if os.environ.get('RUNPOD_WEBHOOK_GET_JOB', None) is None:
        log('RUNPOD_WEBHOOK_GET_JOB not set, switching to get_local', 'WARNING')
        return get_local()

    get_work_url = str(os.environ.get('RUNPOD_WEBHOOK_GET_JOB')
                       ).replace('$ID', worker_id)

    log(f"Requesting job from {get_work_url}")

    headers = {"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"}

    try:
        assigned_job = requests.get(
            get_work_url,
            headers=headers,
            timeout=180
        )
    except requests.exceptions.Timeout:
        log("Timeout while requesting job", 'WARNING')
        return None

    if assigned_job.status_code == 200:
        log(f"TAKE_JOB URL response: {assigned_job.status_code}")
        return assigned_job.json()

    if assigned_job.status_code == 204:
        log(f"TAKE_JOB URL response: {assigned_job.status_code}")
        return None

    log(f"TAKE_JOB URL response: {assigned_job.status_code}", 'ERROR')
    return None


def run(job):
    '''
    Run the job.
    Returns list of URLs and Job Time
    '''
    time_job_started = time.time()
    log(f"Started working on {job['id']} at {time_job_started} UTC")

    model = inference.Model()

    job_output = model.run(job)
    log(f"Job output: {job_output}")

    for index, output in enumerate(job_output):
        log(f"Output {index}: {output}")

        if "error" in output:
            return {
                "error": output["error"]
            }

        # if "image" in output:
        #     object_url = upload.upload_image(job['id'], output["image"], index)
        #     output["image"] = object_url

    job_duration = time.time() - time_job_started
    job_duration_ms = int(job_duration * 1000)

    log(f"Returning as output: {job_output}")

    return {
        "output": job_output,
        "duration_ms": job_duration_ms
    }


def post(worker_id, job_id, job_output, job_time):
    '''
    Complete the job.
    '''
    job_data = {
        "output": job_output
    }

    job_data = json.dumps(job_data, ensure_ascii=False)

    if os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT', None) is None:
        log("RUNPOD_WEBHOOK_POST_OUTPUT not set, skipping completing job", 'WARNING')
        return

    job_done_url = str(os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT'))
    job_done_url = job_done_url.replace('$ID', job_id)
    job_done_url = job_done_url.replace('$RUNPOD_POD_ID', worker_id)

    headers = {
        "charset": "utf-8",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"
    }

    try:
        requests.post(job_done_url, data=job_data, headers=headers, timeout=10)
    except requests.exceptions.Timeout:
        log(f"Timeout while completing job {job_id}")

    log(f"Completed job {job_id} in {job_time} ms")

    return


def error(worker_id, job_id, error_message):
    '''
    Report an error to the job endpoint, marking the job as failed.
    '''
    log(f"Reporting error for job {job_id}: {error_message}", 'ERROR')

    if os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT', None) is None:
        log("RUNPOD_WEBHOOK_POST_OUTPUT not set, skipping erroring job", 'WARNING')
        return

    job_output = {
        "error": error_message
    }

    job_output = json.dumps(job_output, ensure_ascii=False)

    job_error_url = str(os.environ.get('RUNPOD_WEBHOOK_POST_OUTPUT'))
    job_error_url = job_error_url.replace('$ID', job_id)
    job_error_url = job_error_url.replace('$RUNPOD_POD_ID', worker_id)

    headers = {
        "charset": "utf-8",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"
    }

    try:
        requests.post(job_error_url, data=job_output, headers=headers, timeout=10)
    except requests.exceptions.Timeout:
        log(f"Timeout while erroring job {job_id}")

    return


# ------------------------------- Local Testing ------------------------------ #
def get_local():
    '''
    Returns contents of test_inputs.json
    '''
    if not os.path.exists('test_inputs.json'):
        return None

    with open('test_inputs.json', 'r', encoding="UTF-8") as file:
        return json.loads(file.read())
