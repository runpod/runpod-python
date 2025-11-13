"""
runpod | serverless | rp_scale.py
Provides the functionality for scaling the runpod serverless worker.
"""

import asyncio
import signal
import sys
import traceback
from typing import Any, Dict

from ...http_client import AsyncClientSession, ClientSession, TooManyRequests
from .rp_job import get_job, handle_job
from .rp_logger import RunPodLogger
from .worker_state import JobsProgress, IS_LOCAL_TEST

log = RunPodLogger()


def _handle_uncaught_exception(exc_type, exc_value, exc_traceback):
    exc = traceback.format_exception(exc_type, exc_value, exc_traceback)
    log.error(f"Uncaught exception | {exc}")


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

    def __init__(self, config: Dict[str, Any]):
        self._shutdown_event = asyncio.Event()
        self.current_concurrency = 1
        self.config = config
        self.job_progress = JobsProgress()  # Cache the singleton instance

        self.jobs_queue = asyncio.Queue(maxsize=self.current_concurrency)

        self.concurrency_modifier = _default_concurrency_modifier
        self.jobs_fetcher = get_job
        self.jobs_fetcher_timeout = 90
        self.jobs_handler = handle_job

        if concurrency_modifier := config.get("concurrency_modifier"):
            self.concurrency_modifier = concurrency_modifier

        if not IS_LOCAL_TEST:
            # below cannot be changed unless local
            return

        if jobs_fetcher := self.config.get("jobs_fetcher"):
            self.jobs_fetcher = jobs_fetcher

        if jobs_fetcher_timeout := self.config.get("jobs_fetcher_timeout"):
            self.jobs_fetcher_timeout = jobs_fetcher_timeout

        if jobs_handler := self.config.get("jobs_handler"):
            self.jobs_handler = jobs_handler

    async def set_scale(self):
        self.current_concurrency = self.concurrency_modifier(self.current_concurrency)

        if self.jobs_queue and (self.current_concurrency == self.jobs_queue.maxsize):
            # no need to resize
            return

        while self.current_occupancy() > 0:
            # not safe to scale when jobs are in flight
            await asyncio.sleep(1)
            continue

        self.jobs_queue = asyncio.Queue(maxsize=self.current_concurrency)
        log.debug(
            f"JobScaler.set_scale | New concurrency set to: {self.current_concurrency}"
        )

    def start(self):
        """
        This is required for the worker to be able to shut down gracefully
        when the user sends a SIGTERM or SIGINT signal. This is typically
        the case when the worker is running in a container.
        """
        sys.excepthook = _handle_uncaught_exception

        try:
            # Register signal handlers for graceful shutdown
            signal.signal(signal.SIGTERM, self.handle_shutdown)
            signal.signal(signal.SIGINT, self.handle_shutdown)
        except ValueError:
            log.warning("Signal handling is only supported in the main thread.")

        # Start the main loop
        # Run forever until the worker is signalled to shut down.
        asyncio.run(self.run())

    def handle_shutdown(self, signum, frame):
        """
        Called when the worker is signalled to shut down.

        This function is called when the worker receives a signal to shut down, such as
        SIGTERM or SIGINT. It sets the shutdown event, which will cause the worker to
        exit its main loop and shut down gracefully.

        Args:
            signum: The signal number that was received.
            frame: The current stack frame.
        """
        log.debug(f"Received shutdown signal: {signum}.")
        self.kill_worker()

    async def run(self):
        # Create an async session that will be closed when the worker is killed.
        async with AsyncClientSession() as session:
            # Create tasks for getting and running jobs.
            jobtake_task = asyncio.create_task(self.get_jobs(session))
            jobrun_task = asyncio.create_task(self.run_jobs(session))

            tasks = [jobtake_task, jobrun_task]

            # Concurrently run both tasks and wait for both to finish.
            await asyncio.gather(*tasks)

    def is_alive(self):
        """
        Return whether the worker is alive or not.
        """
        return not self._shutdown_event.is_set()

    def kill_worker(self):
        """
        Whether to kill the worker.
        """
        log.debug("Kill worker.")
        self._shutdown_event.set()

    def current_occupancy(self) -> int:
        current_queue_count = self.jobs_queue.qsize()
        current_progress_count = self.job_progress.get_job_count()

        log.debug(
            f"JobScaler.status | concurrency: {self.current_concurrency}; queue: {current_queue_count}; progress: {current_progress_count}"
        )
        return current_progress_count + current_queue_count

    async def get_jobs(self, session: ClientSession):
        """
        Retrieve multiple jobs from the server in batches using blocking requests.

        Runs the block in an infinite loop while the worker is alive.

        Adds jobs to the JobsQueue
        """
        while self.is_alive():
            await self.set_scale()

            jobs_needed = self.current_concurrency - self.current_occupancy()
            if jobs_needed <= 0:
                log.debug("JobScaler.get_jobs | Queue is full. Retrying soon.")
                await asyncio.sleep(1)  # don't go rapidly
                continue

            try:
                log.debug("JobScaler.get_jobs | Starting job acquisition.")

                # Keep the connection to the blocking call with timeout
                acquired_jobs = await asyncio.wait_for(
                    self.jobs_fetcher(session, jobs_needed),
                    timeout=self.jobs_fetcher_timeout,
                )

                if not acquired_jobs:
                    log.debug("JobScaler.get_jobs | No jobs acquired.")
                    continue

                for job in acquired_jobs:
                    await self.jobs_queue.put(job)
                    self.job_progress.add(job)
                    log.debug("Job Queued", job["id"])

                log.info(f"Jobs in queue: {self.jobs_queue.qsize()}")

            except TooManyRequests:
                log.debug(
                    "JobScaler.get_jobs | Too many requests. Debounce for 5 seconds."
                )
                await asyncio.sleep(5)  # debounce for 5 seconds
            except asyncio.CancelledError:
                log.debug("JobScaler.get_jobs | Request was cancelled.")
                raise  # CancelledError is a BaseException
            except asyncio.TimeoutError:
                log.debug("JobScaler.get_jobs | Job acquisition timed out. Retrying.")
            except TypeError as error:
                log.debug(f"JobScaler.get_jobs | Unexpected error: {error}.")
            except Exception as error:
                log.error(
                    f"Failed to get job. | Error Type: {type(error).__name__} | Error Message: {str(error)}"
                )
            finally:
                # Yield control back to the event loop
                await asyncio.sleep(0)

    async def run_jobs(self, session: ClientSession):
        """
        Retrieve jobs from the jobs queue and process them concurrently.

        Runs the block in an infinite loop while the worker is alive or jobs queue is not empty.
        """
        tasks = []  # Store the tasks for concurrent job processing

        while self.is_alive() or not self.jobs_queue.empty():
            # Fetch as many jobs as the concurrency allows
            while len(tasks) < self.current_concurrency and not self.jobs_queue.empty():
                job = await self.jobs_queue.get()

                # Create a new task for each job and add it to the task list
                task = asyncio.create_task(self.handle_job(session, job))
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

    async def handle_job(self, session: ClientSession, job: dict):
        """
        Process an individual job. This function is run concurrently for multiple jobs.
        """
        try:
            log.debug("Handling Job", job["id"])

            await self.jobs_handler(session, self.config, job)

            if self.config.get("refresh_worker", False):
                self.kill_worker()

        except Exception as err:
            log.error(f"Error handling job: {err}", job["id"])
            raise err

        finally:
            # Inform Queue of a task completion
            self.jobs_queue.task_done()

            # Job is no longer in progress
            self.job_progress.remove(job)

            log.debug("Finished Job", job["id"])
