"""@runpod.local_entrypoint: the dev-session entry hook.

    @runpod.local_entrypoint
    async def main():
        result = transcribe.remote("https://...")

the decorated function is registered so `rp dev <module>` can find and
run it (sync or async). importing the module never executes it.
"""

import inspect
from typing import Any, Callable, List, Optional

_ENTRYPOINTS: List[Callable] = []


def local_entrypoint(fn: Callable) -> Callable:
    """register a function as the dev-session entrypoint.

    the function is returned unwrapped so it stays directly callable.
    """
    _ENTRYPOINTS.append(fn)
    return fn


def get_entrypoint() -> Optional[Callable]:
    """the most recently registered entrypoint, if any."""
    return _ENTRYPOINTS[-1] if _ENTRYPOINTS else None


def run_entrypoint(fn: Callable) -> Any:
    """execute an entrypoint, driving the loop for async functions."""
    from .context import block

    result = fn()
    if inspect.isawaitable(result):
        return block(result)
    return result


def _clear_entrypoints() -> None:
    """testing only."""
    _ENTRYPOINTS.clear()
