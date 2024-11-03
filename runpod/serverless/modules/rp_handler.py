"""Retrieve handler info. """

import inspect
from typing import Callable


def is_generator(handler: Callable) -> bool:
    """Check if handler is a generator function."""
    # handler could be an object that has a __call__ method
    if not inspect.isfunction(handler):
        handler = getattr(handler, "__call__", lambda: None)
    return inspect.isgeneratorfunction(handler) or inspect.isasyncgenfunction(handler)


def is_async_generator(handler: Callable) -> bool:
    """Check if handler is an async generator function."""
    # handler could be an object that has a __call__ method
    if not inspect.isfunction(handler):
        handler = getattr(handler, "__call__", lambda: None)
    return inspect.isasyncgenfunction(handler)
