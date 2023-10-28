'''
runpod | serverless | rp_local.py
Provides the local testing functionality for runpod serverless worker.
'''

import os
import sys
import json
from typing import Dict, Any

from runpod.serverless.modules.rp_logger import RunPodLogger
from .rp_job import run_job

log = RunPodLogger()

async def run_local(config: Dict[str, Any]) -> None:
    '''
    Runs the worker locally.
    '''
    # Get the local test job
    if config['rp_args'].get('test_input', None):
        log.info("test_input set, using test_input as job input.")
        local_job = config['rp_args']['test_input']
    else:
        if not os.path.exists("test_input.json"):
            log.warn("test_input.json not found, exiting.")
            sys.exit(1)

        log.info("Using test_input.json as job input.")
        with open("test_input.json", "r", encoding="UTF-8") as file:
            local_job = json.loads(file.read())

    if local_job.get("input", None) is None:
        log.error("Job has no input parameter. Unable to run.")
        sys.exit(1)

    # Set the job ID
    local_job["id"] = local_job.get("id", "local_test")
    log.debug(f"Retrieved local job: {local_job}")

    job_result = await run_job(config["handler"], local_job)

    if job_result.get("error", None):
        log.error(f"Job {local_job['id']} failed with error: {job_result['error']}")
        sys.exit(1)

    log.info(f"Job {local_job['id']} completed successfully.")
    log.info(f"Job result: {job_result}")

    # Compare to sample output, if provided
    if config['rp_args'].get('test_output', None):
        log.info("test_output set, comparing output to test_output.")
        if job_result != config['rp_args']['test_output']:
            log.error("Job output does not match test_output.")
            sys.exit(1)
        log.info("Job output matches test_output.")

    log.info("Local testing complete, exiting.")
    sys.exit(0)
