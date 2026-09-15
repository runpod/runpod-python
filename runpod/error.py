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
