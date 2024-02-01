"""Retrieve handler info. """

import inspect
from typing import Callable


def is_generator(handler: Callable) -> bool:
    """Check if handler is a generator function."""
    return inspect.isgeneratorfunction(handler) or inspect.isasyncgenfunction(handler)
