'''
runpod | serverless | pod_worker.py
Called to convert a container into a worker pod for the runpod serverless platform.
'''

import os

from .modules import lifecycle, job
from .modules.logging import log


def start_worker(config):
    '''
    Starts the worker.
    '''
    worker_life = lifecycle.LifecycleManager()
    worker_life.heartbeat_ping()
    log("Worker lifecycle manager started.", "INFO")

    while True:
        next_job = job.get(worker_life.worker_id)

        try:
            if next_job is None:
                log("No job available before idle timeout.", "INFO")
                continue

            worker_life.job_id = next_job['id']  # Job ID is set by serverless platform.

            if 'input' not in next_job:
                log("No input parameter provided. Erroring out request.", "ERROR")
                job.error(worker_life.worker_id, next_job['id'], "No input provided.")
                continue

            job_results = job.run(next_job, config['handler'])

            if 'error' in job_results:
                job.error(worker_life.worker_id, next_job['id'], job_results['error'])
                continue

            job.post(worker_life.worker_id, next_job['id'], job_results['output'])

        except (KeyError, ValueError, RuntimeError) as err:
            job.error(worker_life.worker_id, next_job['id'], str(err))

        finally:
            worker_life.job_id = None

            if os.environ.get('RUNPOD_WEBHOOK_GET_JOB', None) is None:
                log("Local testing complete, exiting.")
                break  # pylint: disable=lost-exception
