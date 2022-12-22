'''
runpod | serverless | pod_worker.py
Called to convert a container into a worker pod for the runpod serverless platform.
'''

import os
import shutil

from .modules import lifecycle, job
from .modules.logging import log


def start_worker():
    '''
    Starts the worker.
    '''
    worker_life = lifecycle.LifecycleManager()
    worker_life.heartbeat_ping()
    log("Worker lifecycle manager started.")

    while True:
        next_job = job.get(worker_life.worker_id)

        if next_job is not None:
            worker_life.job_id = next_job['id']

            try:
                if 'input' not in next_job:
                    log("No input provided. Erroring out request.", "ERROR")
                    job.error(worker_life.worker_id, next_job['id'], "No input provided.")
                    continue

                job_results = job.run(next_job)

                if 'error' in job_results:
                    job.error(worker_life.worker_id, next_job['id'], job_results['error'])
                    continue

                job.post(
                    worker_life.worker_id,
                    next_job['id'], job_results['output'],
                    job_results['duration_ms']
                )
            except (KeyError, ValueError, RuntimeError) as err:
                job.error(worker_life.worker_id, next_job['id'], str(err))
            finally:
                # -------------------------------- Job Cleanup ------------------------------- #
                shutil.rmtree("input_objects", ignore_errors=True)
                shutil.rmtree("output_objects", ignore_errors=True)

                if os.path.exists('output.zip'):
                    os.remove('output.zip')

                worker_life.job_id = None

        if os.environ.get('RUNPOD_WEBHOOK_GET_JOB', None) is None:
            log("Local testing complete, exiting.")
            break
