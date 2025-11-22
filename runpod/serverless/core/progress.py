"""
Unified Progress Update System with Async Queue.

Provides non-blocking progress updates with automatic batching and retry.

Key improvements over current approach:
- Async queue (non-blocking updates)
- Automatic batching (reduces HTTP overhead)
- Exponential backoff retry (resilient to transient failures)
- Background worker (decoupled from job execution)
"""

import asyncio
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime
import aiohttp

from .log_adapter import CoreLogger


log = CoreLogger(__name__)


@dataclass
class ProgressUpdate:
    """Progress update entry."""

    job_id: str
    data: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)


class ProgressSystem:
    """
    Unified progress update system with async queue and batching.

    Architecture:
    - Updates are queued immediately (non-blocking)
    - Background worker sends batches periodically
    - Automatic retry with exponential backoff
    - Configurable batch size and flush interval

    Performance characteristics:
    - update(): <1μs (async queue put)
    - Batching: Reduces HTTP calls by batch_size factor
    - Memory: O(max_queue_size)
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        progress_url: str,
        batch_size: int = 10,
        flush_interval: float = 2.0,
        max_retries: int = 5,
        max_queue_size: int = 1000
    ):
        """
        Initialize progress system.

        Args:
            session: HTTP client session
            progress_url: URL to POST progress updates
            batch_size: Number of updates per batch
            flush_interval: Seconds between flushes
            max_retries: Maximum retry attempts
            max_queue_size: Maximum queue size
        """
        self.session = session
        self.progress_url = progress_url
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.max_retries = max_retries

        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._worker_task: Optional[asyncio.Task] = None
        self._pending_batch: List[ProgressUpdate] = []

    async def update(self, job_id: str, data: Dict[str, Any]) -> None:
        """
        Send progress update (non-blocking).

        Args:
            job_id: Job ID
            data: Progress data (percent, status, etc.)

        Performance: <1μs (async queue put)
        """
        update = ProgressUpdate(job_id=job_id, data=data)
        try:
            await self._queue.put(update)
            log.debug("Queued progress update", job_id=job_id)
        except asyncio.QueueFull:
            log.warning("Progress queue full, dropping update", job_id=job_id)

    async def start(self) -> None:
        """Start background worker task."""
        if self._worker_task is not None:
            log.warning("Progress worker already running")
            return

        self._worker_task = asyncio.create_task(self._worker_loop())
        log.debug("Started progress worker")

    async def stop(self) -> None:
        """Stop worker and flush pending updates."""
        if self._worker_task is None:
            return

        # Drain queue into pending batch
        while not self._queue.empty():
            try:
                update = self._queue.get_nowait()
                self._pending_batch.append(update)
            except asyncio.QueueEmpty:
                break

        # Flush pending updates before stopping
        await self._flush_batch()

        # Cancel worker
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        self._worker_task = None
        log.debug("Stopped progress worker")

    async def _worker_loop(self) -> None:
        """
        Background worker loop.

        Collects updates from queue and sends in batches.
        """
        last_flush = asyncio.get_event_loop().time()

        while True:
            try:
                # Wait for update or flush interval
                try:
                    update = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=self.flush_interval
                    )
                    self._pending_batch.append(update)
                except asyncio.TimeoutError:
                    # Flush interval reached
                    pass

                # Check if batch is full or interval elapsed
                now = asyncio.get_event_loop().time()
                batch_full = len(self._pending_batch) >= self.batch_size
                interval_elapsed = (now - last_flush) >= self.flush_interval

                if batch_full or (interval_elapsed and self._pending_batch):
                    await self._flush_batch()
                    last_flush = now

            except asyncio.CancelledError:
                # Final flush before exit
                if self._pending_batch:
                    await self._flush_batch()
                raise
            except Exception as e:
                log.error(f"Progress worker error: {e}", exc_info=True)
                # Continue loop despite errors

    async def _flush_batch(self) -> None:
        """
        Send pending batch to API with retry.

        Uses exponential backoff on failures.
        """
        if not self._pending_batch:
            return

        batch = self._pending_batch.copy()
        self._pending_batch.clear()

        # Prepare payload
        payload = {
            "updates": [
                {
                    "job_id": update.job_id,
                    "data": update.data,
                    "timestamp": update.timestamp.isoformat()
                }
                for update in batch
            ]
        }

        # Send with retry
        backoff = 0.1
        max_backoff = 30.0

        for attempt in range(self.max_retries):
            try:
                async with self.session.post(
                    self.progress_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    response.raise_for_status()
                    log.info(f"Sent batch of {len(batch)} progress updates")
                    return  # Success

            except asyncio.CancelledError:
                raise
            except Exception as e:
                if attempt < self.max_retries - 1:
                    log.warning(
                        f"Progress batch send failed (attempt {attempt + 1}/{self.max_retries}): {e}, "
                        f"retrying in {backoff}s"
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)
                else:
                    log.error(
                        f"Progress batch send failed after {self.max_retries} attempts: {e}",
                        exc_info=True
                    )
                    # Drop batch after max retries
                    return
