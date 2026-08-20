"""
runpod | serverless | rp_capture.py

Captures stdout/stderr to report upon handler or prestart failure.
Swaps `sys.stdout`/`sys.stderr` for a tee proxy that writes to both the
real stream and a buffer in a contextvar.

Captured output is attached to failure payloads, which are returned to whoever
called the request, so capture is not on by default. `RUNPOD_LOG_CAPTURE`
selects when it runs:

    auto (default)  capture only when the caller reports registered prestart
                    hooks, so a worker that has not adopted prestart behaves
                    exactly as before
    all             always capture, including handler failures on workers with
                    no prestart hooks
    off             never capture
"""

import contextlib
import contextvars
import io
import os
import sys
from collections.abc import Generator

MAX_CAPTURED_CHARS = 16 * 1024

CAPTURE_AUTO = "auto"
CAPTURE_ALL = "all"
CAPTURE_OFF = "off"
_CAPTURE_MODES = (CAPTURE_AUTO, CAPTURE_ALL, CAPTURE_OFF)

# Capture buffer for the current context
_current: "contextvars.ContextVar[_RingBuffer | None]" = contextvars.ContextVar(
    "rp_stdio_capture", default=None
)


class _RingBuffer:
    """Keeps only the last `limit` characters since the tail is usually where the
    failure reason is."""

    def __init__(self, limit: int = MAX_CAPTURED_CHARS):
        self.limit = limit
        self._buf = ""

    def write(self, text: str) -> int:
        self._buf = (self._buf + text)[-self.limit :]
        return len(text)

    def getvalue(self) -> str:
        return self._buf


class _TeeProxy:
    """Forwards to the real stream and mirrors into its buffer."""

    def __init__(self, real):
        self._real = real

    def write(self, text) -> int:
        n = self._real.write(text)
        buffer = _current.get()
        if buffer is not None:
            with contextlib.suppress(Exception):
                buffer.write(text)
        return n

    def writelines(self, lines) -> None:
        # Not covered by write(); callers that use it would otherwise bypass capture.
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        self._real.flush()

    def __getattr__(self, name):
        # Delegate everything else to the real stream
        return getattr(self._real, name)


io.TextIOBase.register(_TeeProxy)


def capture_mode() -> str:
    """Resolve `RUNPOD_LOG_CAPTURE`, falling back to `auto` on anything unknown."""
    mode = os.environ.get("RUNPOD_LOG_CAPTURE", CAPTURE_AUTO).strip().lower()
    return mode if mode in _CAPTURE_MODES else CAPTURE_AUTO


def install(*, hooks_registered: bool = False) -> None:
    """Install the tee proxy on stdout/stderr, if this worker wants capture.

    Idempotent. `hooks_registered` is what `auto` mode keys off, and the caller
    passes it in so this module stays independent of the prestart registry.
    """
    mode = capture_mode()
    if mode == CAPTURE_OFF:
        return

    if mode == CAPTURE_AUTO and not hooks_registered:
        return

    if not isinstance(sys.stdout, _TeeProxy):
        sys.stdout = _TeeProxy(sys.stdout)
    if not isinstance(sys.stderr, _TeeProxy):
        sys.stderr = _TeeProxy(sys.stderr)


@contextlib.contextmanager
def capture() -> Generator[_RingBuffer]:
    """Capture stdout/stderr written within this context (and within threads it spawns via
    `asyncio.to_thread`), while still passing everything through to the real streams."""
    buffer = _RingBuffer()
    token = _current.set(buffer)
    try:
        yield buffer
    finally:
        # Suppress an abandoned async generator to avoid polluting stderr
        with contextlib.suppress(ValueError):
            _current.reset(token)


@contextlib.contextmanager
def paused() -> Generator[None]:
    """Temporarily stop mirroring logs into the buffer. Currently only used to
    omit diagnostic logs."""
    token = _current.set(None)
    try:
        yield
    finally:
        with contextlib.suppress(ValueError):
            _current.reset(token)


def clip(text: str, limit: int = MAX_CAPTURED_CHARS) -> str:
    """Truncate an error string, keeping the head and tail (the useful parts)."""
    if not text or len(text) <= limit:
        return text
    keep = limit // 2
    omitted = len(text) - 2 * keep
    return f"{text[:keep]}\n...[{omitted} characters truncated]...\n{text[-keep:]}"
