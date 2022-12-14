'''
runpod | serverless | pod_worker.py
Called to convert a container into a worker pod for the runpod serverless platform.
'''

import os
import shutil
import threading

from .modules import lifecycle, job
from .modules.logging import log


def start_worker():
    '''
    Starts the worker.
    '''
    worker_life = lifecycle.LifecycleManager()

    if not worker_life.is_worker_zero:
        log("Not worker zero, starting TTL timer thread.")
        threading.Thread(target=worker_life.check_worker_ttl_thread).start()
    else:
        log("Worker zero, not starting TTL timer thread.")

    while True:
        if os.environ.get('TEST_LOCAL', 'false') != 'true':
            next_job = job.get(worker_life.worker_id)
        else:
            next_job = job.get_local()

        if next_job is not None:
            worker_life.work_in_progress = True  # Rests when "reset_worker_ttl" is called

            try:
                output_urls, job_duration_ms = job.run(
                    next_job['id'], next_job['input'])
                job.post(worker_life.worker_id,
                         next_job['id'], output_urls, job_duration_ms)
            except ValueError as err:
                job.error(worker_life.worker_id, next_job['id'], str(err))

            # -------------------------------- Job Cleanup ------------------------------- #
            shutil.rmtree("input_objects", ignore_errors=True)
            shutil.rmtree("output_objects", ignore_errors=True)

            if os.path.exists('output.zip'):
                os.remove('output.zip')

            worker_life.reset_worker_ttl()

        if os.environ.get('TEST_LOCAL', 'false') == 'true':
            log("Local testing complete, exiting.")
            break
