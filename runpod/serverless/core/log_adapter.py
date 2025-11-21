"""
Logging Adapter for New Core Components.

Bridges Python's standard logging to RunPodLogger format to maintain
backward compatibility with systems that parse the legacy log format.

Key requirements:
- Maintain exact "LEVEL   | job_id | message" format
- Support job ID association for job-related logs
- Handle both endpoint mode (JSON) and standard mode
- Preserve all legacy log levels (TRACE, WARN)
"""

from typing import Optional
from contextvars import ContextVar
from ..modules.rp_logger import RunPodLogger


# Context variable for tracking current job ID
current_job_id: ContextVar[Optional[str]] = ContextVar("current_job_id", default=None)


class CoreLogger:
    """
    Logging adapter that provides Python logging interface while using RunPodLogger.

    This maintains exact compatibility with legacy log format, ensuring downstream
    systems can continue parsing logs correctly.

    Usage:
        # Without job ID
        log = CoreLogger(__name__)
        log.info("Worker started")

        # With job ID context
        log = CoreLogger(__name__)
        with log.job_context("job-123"):
            log.info("Started.")  # Output: INFO   | job-123 | Started.
            log.debug("Processing...")  # Output: DEBUG  | job-123 | Processing...

        # Direct job ID
        log.info("Finished.", job_id="job-123")
    """

    def __init__(self, name: str):
        """
        Initialize logger adapter.

        Args:
            name: Logger name (module name, for compatibility)
        """
        self.name = name
        self._logger = RunPodLogger()

    def _get_job_id(self, job_id: Optional[str] = None) -> Optional[str]:
        """
        Get job ID from context or parameter.

        Args:
            job_id: Explicit job ID (overrides context)

        Returns:
            Job ID if available, None otherwise
        """
        if job_id is not None:
            return job_id
        return current_job_id.get()

    def debug(self, message: str, job_id: Optional[str] = None):
        """Log debug message."""
        self._logger.debug(message, self._get_job_id(job_id))

    def info(self, message: str, job_id: Optional[str] = None):
        """Log info message."""
        self._logger.info(message, self._get_job_id(job_id))

    def warning(self, message: str, job_id: Optional[str] = None):
        """Log warning message."""
        self._logger.warn(message, self._get_job_id(job_id))

    def warn(self, message: str, job_id: Optional[str] = None):
        """Log warning message (alias for compatibility)."""
        self._logger.warn(message, self._get_job_id(job_id))

    def error(self, message: str, job_id: Optional[str] = None, exc_info: bool = False):
        """
        Log error message.

        Args:
            message: Error message
            job_id: Job ID to associate with error
            exc_info: If True, include exception traceback (ignored for compatibility)
        """
        # Note: exc_info is accepted for compatibility but RunPodLogger
        # doesn't support automatic traceback inclusion
        self._logger.error(message, self._get_job_id(job_id))

    def trace(self, message: str, job_id: Optional[str] = None):
        """Log trace message."""
        self._logger.trace(message, self._get_job_id(job_id))

    class JobContext:
        """Context manager for associating logs with a job ID."""

        def __init__(self, job_id: str):
            self.job_id = job_id
            self.token = None

        def __enter__(self):
            self.token = current_job_id.set(self.job_id)
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            current_job_id.reset(self.token)

    def job_context(self, job_id: str) -> "CoreLogger.JobContext":
        """
        Create context manager for job-specific logging.

        Args:
            job_id: Job ID to associate with all logs in context

        Returns:
            Context manager

        Example:
            with log.job_context("job-123"):
                log.info("Started.")  # Output: INFO   | job-123 | Started.
        """
        return self.JobContext(job_id)
