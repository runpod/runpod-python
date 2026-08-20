"""
runpod | serverless | rp_scale.py
Provides the functionality for scaling the runpod serverless worker.
"""

import asyncio
import json
import signal
import sys
import traceback
from typing import Any

from ...http_client import AsyncClientSession, ClientSession, TooManyRequests
from .rp_http import send_result
from .rp_job import _job_stop_url, get_job, get_stop_signals, handle_job
from .rp_logger import RunPodLogger, _reset_batch_id, _set_batch_id
from .rp_prestart import get_prestart_hooks, run_prestart_phase
from .worker_state import IS_LOCAL_TEST, JobsProgress

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

    def __init__(self, config: dict[str, Any]):
        self._shutdown_event = asyncio.Event()
        self._prestart_ready = asyncio.Event()
        self._prestart_error: dict[str, Any] | None = None
        # Whether any job-take has returned a request, which decides if a
        # prestart failure still needs to claim one to report itself against.
        self._took_a_request = False
        self.current_concurrency = 1
        self.config = config
        self.prestart_hooks = get_prestart_hooks()
        self.job_progress = JobsProgress()  # Cache the singleton instance

        # maps in-progress job ids to their running tasks so individual jobs
        # can be stopped without killing the whole worker
        self.jobs_tasks: dict[str, asyncio.Task[Any]] = {}

        self.stop_signals_fetcher = get_stop_signals
        self.stop_signals_fetcher_timeout = 90

        self.jobs_queue = asyncio.Queue(maxsize=self.current_concurrency)

        self.concurrency_modifier = _default_concurrency_modifier
        self.jobs_fetcher = get_job
        self.jobs_fetcher_timeout = 90
        self.prestart_claim_timeout = 10
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

        if stop_signals_fetcher := self.config.get("stop_signals_fetcher"):
            self.stop_signals_fetcher = stop_signals_fetcher

        if stop_signals_fetcher_timeout := self.config.get(
            "stop_signals_fetcher_timeout"
        ):
            self.stop_signals_fetcher_timeout = stop_signals_fetcher_timeout

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
            log.warn("Signal handling is only supported in the main thread.")

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
        """Run prestart and the three persistent request loops concurrently."""
        async with AsyncClientSession() as session:
            # Keep prestart outside the request-loop gather. A hook may have no
            # timeout, so request-loop shutdown must be able to cancel it.
            prestart_task = asyncio.create_task(self._run_prestart())
            request_loop_tasks = (
                asyncio.create_task(self.get_jobs(session)),
                asyncio.create_task(self.run_jobs(session)),
                asyncio.create_task(self.monitor_stop_signals(session)),
            )
            request_loops_future = asyncio.gather(*request_loop_tasks)

            try:
                # Wait for either lifecycle to end without cancelling the other.
                done, _ = await asyncio.wait(
                    {prestart_task, request_loops_future},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if prestart_task in done:
                    try:
                        await prestart_task
                    except BaseException:
                        # Normal prestart failures are handled inside _run_prestart.
                        self.kill_worker()
                        raise

                # Prestart completion does not end the worker; request loops do.
                await request_loops_future
            finally:
                # Clean up every task before closing their shared HTTP session.
                for task in request_loop_tasks:
                    if not task.done():
                        task.cancel()
                if not prestart_task.done():
                    prestart_task.cancel()
                await asyncio.gather(
                    *request_loop_tasks, prestart_task, return_exceptions=True
                )
                await asyncio.gather(request_loops_future, return_exceptions=True)

        # Normal prestart failures are reported and drained before forced respawn.
        if self._prestart_error is not None:
            from .rp_fitness import _terminate_unhealthy

            _terminate_unhealthy(1)

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

    async def _claim_one_job_to_fail(self, session: ClientSession) -> None:
        """Prestart failed before this worker held a request. Claim one queued
        request and fail it with the reason.

        Without this, an instant failure never reaches a caller. The worker exits,
        the platform respawns it into the same failure, and the request that
        triggered the scale-up waits out its queue TTL with no explanation.
        """
        payload = self._prestart_error
        if payload is None:
            return
        try:
            jobs = await asyncio.wait_for(
                self.jobs_fetcher(session, 1), timeout=self.prestart_claim_timeout
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - reporting must not mask the failure
            log.debug(f"JobScaler.get_jobs | No request claimed to fail: {error}")
            return

        for job in jobs or []:
            await self._fail_job(session, job, payload)

    async def get_jobs(self, session: ClientSession):
        """
        Retrieve multiple jobs from the server in batches using blocking requests.

        Runs the block in an infinite loop while the worker is alive.

        Adds jobs to the JobsQueue
        """
        while self.is_alive():
            if self._prestart_error is not None:
                # Prestart failure is terminal for this worker. Draining requests
                # already held by the worker is owned by run_jobs.
                break
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

                self._took_a_request = True

                if self._prestart_error is not None:
                    # Fail every request acquired while prestart was running.
                    for job in acquired_jobs:
                        await self._fail_job(session, job, self._prestart_error)
                    return

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

        if self._prestart_error is not None and not self._took_a_request:
            # No job-take was in flight when prestart failed. Make one bounded
            # take so the request that scaled this worker receives the startup
            # error instead of waiting for its queue TTL.
            await self._claim_one_job_to_fail(session)

    async def run_jobs(self, session: ClientSession):
        """
        Retrieve jobs from the jobs queue and process them concurrently.

        Runs the block in an infinite loop while the worker is alive or jobs queue is not empty.
        """
        tasks: set[asyncio.Task[Any]] = set()

        last_task_count = 0
        while self.is_alive() or not self.jobs_queue.empty():
            # Fetch as many jobs as the concurrency allows
            while len(tasks) < self.current_concurrency and not self.jobs_queue.empty():
                job = await self.jobs_queue.get()
                # Create a new task for each job and track it by job id
                task = asyncio.create_task(self.handle_job(session, job))
                tasks.add(task)
                self.jobs_tasks[job["id"]] = task

            # Wait for any job to finish
            if tasks:
                current_task_count = len(tasks)
                if current_task_count != last_task_count:
                    log.info(f"Jobs in progress: {current_task_count}")
                    last_task_count = current_task_count

                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED, timeout=0.1
                )

                # Remove completed tasks from the list
                tasks.difference_update(done)

            else:
                # don't busy wait
                await asyncio.sleep(0.1)

        # Ensure all remaining tasks finish before stopping. Stopped jobs raise
        # CancelledError during this drain, which is expected, but a genuine
        # handler error must not be silently discarded.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) and not isinstance(
                result, asyncio.CancelledError
            ):
                log.error(
                    f"JobScaler.run_jobs | Task failed during shutdown drain: {result}"
                )

    async def monitor_stop_signals(self, session: ClientSession):
        """
        Long-polls the dedicated stop channel and stops signalled jobs.

        Runs in an infinite loop while the worker is alive. The Runpod server
        signals a request to be stopped (for example when it is cancelled or
        times out) and this loop stops just that in-progress job, leaving the
        worker's other jobs running.
        """
        if self.stop_signals_fetcher is get_stop_signals and _job_stop_url() is None:
            log.warn(
                "JobScaler.monitor_stop_signals | Stop channel could not be derived "
                "from the job-take URL; per-job stop is disabled for this worker."
            )
            return

        while self.is_alive():
            try:
                # Bound the long-poll so shutdown is not blocked by the shared
                # session's much longer default timeout.
                job_ids = await asyncio.wait_for(
                    self.stop_signals_fetcher(session),
                    timeout=self.stop_signals_fetcher_timeout,
                )
                for job_id in job_ids:
                    await self.stop_job(job_id)

                if not job_ids:
                    # floor delay so the loop can't busy-spin when the server
                    # returns immediately instead of holding the poll open
                    await asyncio.sleep(1)
            except TooManyRequests:
                await asyncio.sleep(5)  # debounce
            except asyncio.CancelledError:
                log.debug("JobScaler.monitor_stop_signals | Request was cancelled.")
                raise  # CancelledError is a BaseException
            except asyncio.TimeoutError:
                log.debug(
                    "JobScaler.monitor_stop_signals | Stop poll timed out. Retrying."
                )
            except Exception as error:
                log.error(
                    f"JobScaler.monitor_stop_signals | Error Type: {type(error).__name__} | Error Message: {str(error)}"
                )
                await asyncio.sleep(1)  # don't spin on persistent errors
            finally:
                await asyncio.sleep(0)

    async def stop_job(self, job_id: str) -> bool:
        """
        Stop a single in-progress job by cancelling its running task.

        Args:
            job_id: The id of the job to stop.

        Returns:
            True if a matching in-progress job was found and stopped,
            False otherwise.
        """
        task = self.jobs_tasks.get(job_id)
        if task is None:
            log.debug(f"JobScaler.stop_job | No in-progress job for {job_id}.")
            return False

        log.info("Stopping job.", job_id)
        task.cancel()
        return True

    async def handle_job(self, session: ClientSession, job: dict[str, Any]):
        """
        Process an individual job. This function is run concurrently for multiple jobs.
        """
        batch_id_token = _set_batch_id(job.get("batchId"))
        try:
            log.debug("Handling Job", job["id"])

            # Hold the handler until every registered prestart hook finishes.
            if self.prestart_hooks:
                if not await self._wait_for_prestart():
                    log.warn(
                        "Shutting down before prestart finished; leaving this "
                        "request for another worker.",
                        job["id"],
                    )
                    return

                if self._prestart_error is not None:
                    await self._fail_job(session, job, self._prestart_error)
                    return

            await self.jobs_handler(session, self.config, job)

            if self.config.get("refresh_worker", False):
                self.kill_worker()

        except asyncio.CancelledError:
            log.info("Job stopped.", job["id"])
            raise  # CancelledError is a BaseException

        except Exception as err:
            log.error(f"Error handling job: {err}", job["id"])
            raise

        finally:
            # Inform Queue of a task completion
            self.jobs_queue.task_done()

            # Job is no longer in progress
            self.job_progress.remove(job)
            self.jobs_tasks.pop(job["id"], None)

            log.debug("Finished Job", job["id"])
            _reset_batch_id(batch_id_token)

    async def _wait_for_prestart(self) -> bool:
        """Wait for prestart to finish or for worker shutdown to begin."""
        ready = asyncio.create_task(self._prestart_ready.wait())
        stopping = asyncio.create_task(self._shutdown_event.wait())
        try:
            await asyncio.wait({ready, stopping}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            ready.cancel()
            stopping.cancel()
        return self._prestart_ready.is_set()

    async def _fail_job(
        self, session: ClientSession, job: dict[str, Any], payload: dict[str, Any]
    ):
        """Fail a request with a structured startup error."""
        log.error(f"Failing job due to prestart failure. | {job['id']}")
        await send_result(session, {"error": json.dumps(payload)}, job, is_stream=False)

    async def _run_prestart(self):
        """Run hooks beside queue intake, then open the handler gate."""
        try:
            self._prestart_error = await run_prestart_phase(
                self.prestart_hooks, self.config.get("prestart_timeout")
            )
        finally:
            # Always release held handlers.
            self._prestart_ready.set()

        if self._prestart_error is not None:
            # Stop long-running loops before waiting for acquired requests to drain.
            self.kill_worker()
            while self.current_occupancy() > 0:
                await asyncio.sleep(0.1)
