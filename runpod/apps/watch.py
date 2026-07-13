"""filesystem change detection for dev sessions.

a simple mtime poller: no extra dependency, no platform-specific event
apis, good enough for the dev loop's granularity.
"""

import time
from pathlib import Path
from typing import Dict, Iterable, Optional

from .discovery import _SKIP_DIRS


def _snapshot(roots: Iterable[Path]) -> Dict[str, float]:
    state: Dict[str, float] = {}
    for root in roots:
        if root.is_file():
            try:
                state[str(root)] = root.stat().st_mtime
            except OSError:
                # file deleted mid-scan; picked up on the next pass
                pass
            continue
        for path in root.rglob("*.py"):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            try:
                state[str(path)] = path.stat().st_mtime
            except OSError:
                continue
    return state


class FileWatcher:
    """tracks python file changes under a set of roots."""

    def __init__(self, roots: Iterable[Path]):
        self.roots = [Path(r) for r in roots]
        self._state = _snapshot(self.roots)

    def changed(self) -> bool:
        """true if any python file was added, removed, or modified since
        the last call that returned true (or since construction)."""
        current = _snapshot(self.roots)
        if current != self._state:
            self._state = current
            return True
        return False

    def wait_for_change(
        self, poll_interval: float = 0.5, timeout: Optional[float] = None
    ) -> bool:
        """block until a change is detected. returns false on timeout."""
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            if self.changed():
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(poll_interval)
