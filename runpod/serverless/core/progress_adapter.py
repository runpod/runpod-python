"""
Progress Update Adapter for Legacy Compatibility.

Provides a singleton ProgressSystem that adapts the legacy progress_update(job, progress)
API to the new ProgressSystem.update(job_id, progress) interface.

This adapter ensures zero breaking changes for users calling:
    runpod.serverless.progress_update(job, progress)
"""

import asyncio
import logging
import threading
from typing import Any, Dict, Optional

from ...http_client import AsyncClientSession
from .progress import ProgressSystem


log = logging.getLogger(__name__)


class ProgressAdapter:
    """
    Singleton adapter for progress updates.

    Lazily initializes a ProgressSystem when first progress update is sent.
    Thread-safe singleton pattern ensures only one instance exists.
    """

    _instance: Optional['ProgressAdapter'] = None
    _lock = threading.Lock()

    def __new__(cls):
        """Thread-safe singleton instantiation."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize adapter (only runs once due to singleton)."""
        if self._initialized:
            return

        self._progress_system: Optional[ProgressSystem] = None
        self._session: Optional[AsyncClientSession] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._initialized = True

    async def _ensure_initialized(self):
        """Lazy initialization of ProgressSystem."""
        if self._progress_system is not None:
            return

        # Get progress URL from environment
        import os
        progress_url = os.environ.get("RUNPOD_WEBHOOK_POST_OUTPUT")

        if not progress_url:
            log.warning("No RUNPOD_WEBHOOK_POST_OUTPUT set, progress updates disabled")
            return

        # Create session if needed
        if self._session is None:
            self._session = AsyncClientSession()

        # Create progress system
        self._progress_system = ProgressSystem(
            session=self._session,
            progress_url=progress_url,
            batch_size=int(os.getenv("RUNPOD_PROGRESS_BATCH_SIZE", "10")),
            flush_interval=float(os.getenv("RUNPOD_PROGRESS_FLUSH_INTERVAL", "1.0")),
        )

        await self._progress_system.start()
        log.info("ProgressSystem initialized for legacy compatibility")

    def progress_update(self, job: Dict[str, Any], progress: Any) -> None:
        """
        Legacy-compatible progress update function.

        Maps the old API: progress_update(job, progress)
        To new API: ProgressSystem.update(job_id, progress)

        Args:
            job: Job dictionary containing at minimum {"id": str}
            progress: Progress data (can be dict, str, int, etc.)

        Note: This function is synchronous but schedules async work.
        """
        if not isinstance(job, dict) or "id" not in job:
            log.warning(f"Invalid job format for progress update: {job}")
            return

        job_id = job["id"]

        # Normalize progress to dict if needed
        if not isinstance(progress, dict):
            progress_data = {"progress": progress}
        else:
            progress_data = progress

        # Get or create event loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop, create new one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._event_loop = loop

        # Schedule the async progress update
        if loop.is_running():
            # Running in async context, schedule as task
            asyncio.create_task(self._async_progress_update(job_id, progress_data))
        else:
            # Not in async context, run directly
            loop.run_until_complete(self._async_progress_update(job_id, progress_data))

    async def _async_progress_update(self, job_id: str, progress: Dict[str, Any]):
        """Internal async progress update."""
        await self._ensure_initialized()

        if self._progress_system is None:
            # Progress system disabled
            return

        await self._progress_system.update(job_id, progress)

    async def shutdown(self):
        """Shutdown progress system and clean up resources."""
        if self._progress_system is not None:
            await self._progress_system.stop()
            self._progress_system = None

        if self._session is not None:
            await self._session.close()
            self._session = None

        log.info("ProgressAdapter shut down")


# Global singleton instance for legacy compatibility
_adapter = ProgressAdapter()


def progress_update(job: Dict[str, Any], progress: Any) -> None:
    """
    Legacy-compatible progress update function.

    This function maintains the exact signature of the old progress_update API
    while routing to the new ProgressSystem internally.

    Args:
        job: Job dictionary containing at minimum {"id": str}
        progress: Progress data (can be dict, str, int, etc.)

    Example:
        >>> progress_update(job, {"percent": 50, "status": "processing"})
        >>> progress_update(job, "halfway done")
        >>> progress_update(job, 50)
    """
    _adapter.progress_update(job, progress)


async def shutdown_progress_adapter():
    """Shutdown the global progress adapter (for cleanup)."""
    await _adapter.shutdown()
