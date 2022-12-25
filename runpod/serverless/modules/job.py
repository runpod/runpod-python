''' PodWorker | jobs.py '''

import os
import time
import json
import requests

from .logging import log


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


def run(job, run_handler):
    '''
    Run the job.
    Returns list of URLs and Job Time
    '''
    log(f"Started working on {job['id']} at {time.time()} UTC")

    job_output = run_handler(job)

    log(f"Job output: {job_output}")

    for index, output in enumerate(job_output):
        log(f"Output {index}: {output}")

        if "error" in output:
            return {
                "error": output["error"]
            }

    log(f"Returning as output: {job_output}")

    return {
        "output": job_output,
    }


def post(worker_id, job_id, job_output):
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

    log(f"Completed job {job_id}")

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
