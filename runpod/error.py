"""
runpod | error.py

This file contains the error classes for the runpod package.
"""

from typing import Optional


class RunPodError(Exception):
    """
    Base class for all runpod errors
    """

    def __init__(self, message: Optional[str] = None):
        super().__init__(message)
        self.message = message

    def __str__(self):
        if self.message:
            return self.message
        return super().__str__()


class AuthenticationError(RunPodError):
    """
    Raised when authentication fails
    """


class QueryError(RunPodError):
    """
    Raised when an API request fails
    """

    def __init__(
        self,
        message: Optional[str] = None,
        query: Optional[str] = None,
        status_code: Optional[int] = None,
        errors: Optional[list[str]] = None,
    ):
        super().__init__(message)
        self.query = query
        self.status_code = status_code
        self.errors = errors or []


class RateLimitError(QueryError):
    """A REST quota exceeds the request's retry or wait budget."""

    def __init__(
        self,
        message: str,
        query: str,
        retry_after: float,
        errors: Optional[list[str]] = None,
    ):
        super().__init__(message, query, status_code=429, errors=errors)
        self.retry_after = retry_after


class JobError(RunPodError):
    """A serverless job ended FAILED, TIMED_OUT or CANCELLED.

    Raised by Endpoint.run_sync and Job.output instead of returning None,
    so a failed job can't be mistaken for a handler that returned None.
    """

    def __init__(self, job_id: str, status: str, error: Optional[str] = None):
        detail = f": {error}" if error else ""
        super().__init__(f"job {job_id} {status}{detail}")
        self.job_id = job_id
        self.status = status
        self.error = error
