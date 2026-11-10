"""@runpod.local_entrypoint: the dev-session entry hook.

    @runpod.local_entrypoint
    async def main():
        result = transcribe.remote("https://...")

the decorated function is registered so `rp flash dev <module>` can find and
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


def get_entrypoints() -> List[Callable]:
    """return the registered entry hooks."""
    return list(_ENTRYPOINTS)


def select_entrypoint(selector: Optional[str] = None) -> Optional[Callable]:
    """select a registered hook by module:function, requiring an unambiguous choice."""
    named = [(f"{fn.__module__}:{fn.__name__}", fn) for fn in _ENTRYPOINTS]

    if selector is not None:
        matches = [fn for name, fn in named if name == selector]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            available = ", ".join(name for name, _ in named) or "none"
            raise ValueError(
                f"entrypoint '{selector}' not found; available: {available}"
            )
    elif len(named) <= 1:
        return named[0][1] if named else None

    raise ValueError(
        "multiple @runpod.local_entrypoint hooks found: "
        + ", ".join(name for name, _ in named)
        + "; select one with --entrypoint module:function"
    )


def _restore_entrypoints(entrypoints: List[Callable]) -> None:
    """restore the entry hook registry."""
    _ENTRYPOINTS[:] = entrypoints


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
