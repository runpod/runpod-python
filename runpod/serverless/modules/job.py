''' PodWorker | jobs.py '''

import os
import time
import json
import requests

from .logging import log

rp_session = requests.Session()
rp_session.headers.update({"Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"})


def get(worker_id):
    '''
    Get next job from job endpoint, returns job json.
    The job format is:
    {
        "id": {job_id},
        "input": {job_input}
    }
    '''
    get_return = None

    try:
        if os.environ.get('RUNPOD_WEBHOOK_GET_JOB', None) is None:
            log('RUNPOD_WEBHOOK_GET_JOB not set, switching to get_local', 'WARNING')
            get_return = get_local()

        else:
            get_work_url = str(os.environ.get('RUNPOD_WEBHOOK_GET_JOB')).replace('$ID', worker_id)

            log(f"Requesting job from {get_work_url}")

            assigned_job = rp_session.get(
                get_work_url,
                timeout=180
            )

            if assigned_job.status_code == 200:
                log(f"TAKE_JOB URL response: {assigned_job.status_code}")
                get_return = assigned_job.json()

    # Status code 400
    except requests.exceptions.HTTPError:
        log("HTTPError while requesting job", 'WARNING')

    # Status code 408
    except requests.exceptions.Timeout:
        log("Timeout while requesting job", 'WARNING')

    finally:
        log(f"GET_JOB URL response: {get_return}", "DEBUG")

        return get_return  # pylint: disable=lost-exception


def run(job, run_handler):
    '''
    Run the job.
    Returns the job output.
    '''
    log(f"Started working on {job['id']} at {time.time()} UTC", "INFO")

    run_return = {
        "error": "Failed to return job output or capture error."
    }

    try:
        job_output = run_handler(job)

        if "error" in job_output:
            run_return = {
                "error": job_output['error']
            }
        else:
            run_return = {
                "output": job_output
            }

    except Exception as err:    # pylint: disable=broad-except
        log(f"Error while running job {job['id']}: {err}", "ERROR")

        run_return = {
            "error": str(err)
        }

    finally:
        log(f"Finished working on {job['id']} at {time.time()} UTC", "INFO")
        log(f"Run Returning: {run_return}", "INFO")

        return run_return  # pylint: disable=lost-exception


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
        # "Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"
    }

    try:
        rp_session.post(job_done_url, data=job_data, headers=headers, timeout=10)

    # Status code 400
    except requests.exceptions.HTTPError:
        log(f"HTTPError while completing job {job_id}")

    # Status code 408
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
        # "Authorization": f"{os.environ.get('RUNPOD_AI_API_KEY')}"
    }

    try:
        rp_session.post(job_error_url, data=job_output, headers=headers, timeout=10)
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
