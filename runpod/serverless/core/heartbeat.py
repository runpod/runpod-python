"""
Async Heartbeat System.

Lightweight heartbeat task running in the main event loop, eliminating
the 50-200MB memory overhead of the current multiprocessing approach.

Key improvements:
- Runs as async task (no separate process)
- Direct memory access to JobState (no file I/O)
- Exponential backoff on failures
- Configurable ping interval
- Graceful start/stop lifecycle
"""

import asyncio
from typing import Optional
import aiohttp

from ...version import __version__ as runpod_version
from .job_state import JobState
from .log_adapter import CoreLogger


log = CoreLogger(__name__)


class Heartbeat:
    """
    Async heartbeat task in main event loop.

    Sends periodic pings to the Runpod platform indicating worker health
    and reporting active job IDs. Runs independently of job processing.

    Benefits over multiprocessing approach:
    - Memory: -50-200MB (no process duplication)
    - Latency: Direct memory access (no file I/O)
    - Debugging: Single process, easier to trace
    - Reliability: Exponential backoff on failures
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        job_state: JobState,
        ping_url: str,
        interval: int = 10
    ):
        """
        Initialize heartbeat.

        Args:
            session: Shared aiohttp session for HTTP requests
            job_state: Job state manager (in-memory access)
            ping_url: Endpoint URL for heartbeat pings
            interval: Ping interval in seconds (default: 10)
        """
        self.session = session
        self.job_state = job_state
        self.ping_url = ping_url
        self.interval = interval
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """
        Start heartbeat async task.

        Creates background task that runs until cancelled.
        If task is already running, logs warning and returns.
        """
        if self._task is not None:
            log.warning("Heartbeat task already running")
            return

        self._task = asyncio.create_task(self._ping_loop())
        log.debug(
            f"Started heartbeat task (interval: {self.interval}s, url: {self.ping_url})"
        )

    async def stop(self) -> None:
        """
        Stop heartbeat async task.

        Gracefully cancels the background task and waits for cleanup.
        """
        if self._task is None:
            return

        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        log.debug("Stopped heartbeat task")

    async def _ping_loop(self) -> None:
        """
        Background ping loop with exponential backoff.

        Runs continuously, sending pings at regular intervals.
        On failure, backs off exponentially: 1s, 2s, 4s, 8s, etc.
        Resets backoff to 1s on successful ping.
        """
        backoff = 1
        max_backoff = 60

        while True:
            try:
                await self._send_ping()

                # Reset backoff on success
                backoff = 1

                # Wait for next interval
                await asyncio.sleep(self.interval)

            except asyncio.CancelledError:
                log.debug("Heartbeat loop cancelled, exiting")
                raise

            except Exception as e:
                log.warning(f"Heartbeat failed: {e}, backing off {backoff}s")

                # Exponential backoff
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    async def _send_ping(self) -> None:
        """
        Send single ping to platform.

        Reads job list from memory (no file I/O) and sends HTTP GET
        with job IDs as query parameter.

        Raises:
            aiohttp.ClientError: On HTTP errors
            asyncio.TimeoutError: On timeout
        """
        # Direct memory access - NO file I/O
        job_ids = self.job_state.get_job_list()

        params = {
            "job_id": job_ids or "",
            "runpod_version": runpod_version,
            "retry_ping": "0"
        }

        # Timeout is 2x interval to allow for network delays
        timeout = self.interval * 2

        async with self.session.get(
            self.ping_url,
            params=params,
            timeout=timeout
        ) as response:
            response.raise_for_status()
            log.debug(f"Heartbeat Sent | URL: {self.ping_url} | Status: {response.status}")
