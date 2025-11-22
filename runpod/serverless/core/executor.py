"""
Automatic Executor Detection for CPU-Blocking Handlers.

Provides automatic detection and execution of handlers in appropriate context:
- Async handlers: Execute directly in event loop
- Sync handlers: Execute in thread pool to prevent blocking

This prevents CPU-blocking sync handlers from blocking the event loop,
ensuring optimal performance for both async and sync workloads.
"""

import asyncio
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Any

from .log_adapter import CoreLogger


log = CoreLogger(__name__)


class JobExecutor:
    """
    Automatic executor detection and execution system.

    Architecture:
    - Detects async vs sync handlers automatically
    - Async handlers run directly in event loop
    - Sync handlers run in thread pool (prevents blocking)
    - Configurable thread pool size

    Performance characteristics:
    - Async handlers: No overhead (direct execution)
    - Sync handlers: Thread pool overhead (~1-5ms per call)
    - Event loop protection: Prevents blocking from sync work
    """

    def __init__(self, max_workers: int = None):
        """
        Initialize job executor.

        Args:
            max_workers: Maximum thread pool workers (default: CPU count)
        """
        if max_workers is None:
            # Default to CPU count for thread pool
            try:
                max_workers = multiprocessing.cpu_count()
            except NotImplementedError:
                max_workers = 4

        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        log.debug(f"Initialized JobExecutor with {max_workers} workers")

    def is_async_handler(self, handler: Callable) -> bool:
        """
        Detect if handler is async.

        Args:
            handler: Handler function

        Returns:
            True if async, False if sync
        """
        return asyncio.iscoroutinefunction(handler)

    async def execute(self, handler: Callable, job: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute handler in appropriate context.

        Async handlers execute directly in event loop.
        Sync handlers execute in thread pool to avoid blocking.

        Args:
            handler: Handler function (async or sync)
            job: Job data

        Returns:
            Handler result

        Raises:
            RuntimeError: If executor is shut down
            Exception: Any exception raised by handler
        """
        if self._executor is None:
            raise RuntimeError("Executor has been shut down")

        job_id = job.get('id', 'unknown')

        if self.is_async_handler(handler):
            # Async handler - execute directly in event loop
            log.debug("Executing async handler", job_id=job_id)
            return await handler(job)
        else:
            # Sync handler - execute in thread pool
            log.debug("Executing sync handler in thread pool", job_id=job_id)
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(self._executor, handler, job)

    def shutdown(self, wait: bool = True) -> None:
        """
        Shutdown thread pool.

        Args:
            wait: Wait for pending tasks to complete
        """
        if self._executor is not None:
            self._executor.shutdown(wait=wait)
            self._executor = None
            log.info("JobExecutor shut down")
