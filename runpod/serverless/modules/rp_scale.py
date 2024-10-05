"""
runpod | serverless | rp_scale.py
Provides the functionality for scaling the runpod serverless worker.
"""

import asyncio
from typing import Any, Dict

from ...http_client import ClientSession
from .rp_job import get_job, handle_job
from .rp_logger import RunPodLogger
from .worker_state import JobsQueue, JobsProgress

log = RunPodLogger()
job_list = JobsQueue()
job_progress = JobsProgress()


def _default_concurrency_modifier(current_concurrency: int) -> int:
    """
    Default concurrency modifier.

    This function returns the current concurrency without any modification.

    Args:
        current_concurrency (int): The current concurrency.

    Returns:
        int: The current concurrency.
    """
    return current_concurrency


class JobScaler:
    """
    Job Scaler. This class is responsible for scaling the number of concurrent requests.
    """

    def __init__(self, concurrency_modifier: Any):
        if concurrency_modifier is None:
            self.concurrency_modifier = _default_concurrency_modifier
        else:
            self.concurrency_modifier = concurrency_modifier

        self.current_concurrency = 1
        self._is_alive = True

    def is_alive(self):
        """
        Return whether the worker is alive or not.
        """
        return self._is_alive

    def kill_worker(self):
        """
        Whether to kill the worker.
        """
        self._is_alive = False

    async def get_jobs(self, session: ClientSession):
        """
        Retrieve multiple jobs from the server in batches using blocking requests.

        Runs the block in an infinite loop while the worker is alive.

        Adds jobs to the JobsQueue
        """
        while self.is_alive():
            log.debug(f"Jobs in progress: {job_progress.get_job_count()}")

            try:
                self.current_concurrency = self.concurrency_modifier(
                    self.current_concurrency
                )
                log.debug(f"Concurrency set to: {self.current_concurrency}")

                jobs_needed = self.current_concurrency - job_progress.get_job_count()
                if not jobs_needed:  # zero or less
                    log.debug("Queue is full. Retrying soon.")
                    continue

                acquired_jobs = await get_job(session, jobs_needed)
                if not acquired_jobs:
                    log.debug("No jobs acquired.")
                    continue

                for job in acquired_jobs:
                    await job_list.add_job(job)

                log.info(f"Jobs in queue: {job_list.get_job_count()}")

            except Exception as error:
                log.error(
                    f"Failed to get job. | Error Type: {type(error).__name__} | Error Message: {str(error)}"
                )

            finally:
                await asyncio.sleep(5)  # yield control back to the event loop

    async def run_jobs(self, session: ClientSession, config: Dict[str, Any]):
        """
        Retrieve jobs from the jobs queue and process them concurrently.

        Runs the block in an infinite loop while the worker is alive or jobs queue is not empty.
        """
        tasks = []  # Store the tasks for concurrent job processing

        while self.is_alive() or not job_list.empty():
            # Fetch as many jobs as the concurrency allows
            while len(tasks) < self.current_concurrency and not job_list.empty():
                job = await job_list.get_job()

                # Create a new task for each job and add it to the task list
                task = asyncio.create_task(self.handle_job(session, config, job))
                tasks.append(task)

            # Wait for any job to finish
            if tasks:
                log.info(f"Jobs in progress: {len(tasks)}")

                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )

                # Remove completed tasks from the list
                tasks = [t for t in tasks if t not in done]

            # Yield control back to the event loop
            await asyncio.sleep(0)

        # Ensure all remaining tasks finish before stopping
        await asyncio.gather(*tasks)

    async def handle_job(self, session: ClientSession, config: Dict[str, Any], job):
        """
        Process an individual job. This function is run concurrently for multiple jobs.
        """
        log.debug(f"Processing job: {job}")
        job_progress.add(job)

        try:
            await handle_job(session, config, job)

            if config.get("refresh_worker", False):
                self.kill_worker()
        
        except Exception as err:
            log.error(f"Error handling job: {err}", job["id"])
            raise err

        finally:
            # Inform JobsQueue of a task completion
            job_list.task_done()

            # Job is no longer in progress
            job_progress.remove(job["id"])
