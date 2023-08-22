'''
runpd | error.py

This file contains the error classes for the runpod package.
'''

from typing import Optional


class RunPodError(Exception):
    '''
    Base class for all runpod errors
    '''
    def __init__(self, message: Optional[str] = None):
        super().__init__(message)

        self.message = message



class AuthenticationError(RunPodError):
    '''
    Raised when authentication fails
    '''


class QueryError(RunPodError):
    '''
    Raised when a GraphQL query fails
    '''
