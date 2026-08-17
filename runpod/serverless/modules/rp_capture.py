"""
runpod | serverless | rp_capture.py

Captures stdout/stderr, to be reported upon handler or initializer failure.
Swaps `sys.stdout`/`sys.stderr` for a tee proxy that writes to both the
real stream and a buffer in a contextvar.
"""

import contextlib
import contextvars
import sys
from collections.abc import Generator

MAX_CAPTURED_CHARS = 16 * 1024

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

    def flush(self) -> None:
        self._real.flush()

    def __getattr__(self, name):
        # Delegate everything else to the real stream
        return getattr(self._real, name)


def install() -> None:
    """Install the tee proxy on stdout/stderr. Idempotent."""
    if not isinstance(sys.stdout, _TeeProxy):
        sys.stdout = _TeeProxy(sys.stdout)
    if not isinstance(sys.stderr, _TeeProxy):
        sys.stderr = _TeeProxy(sys.stderr)


@contextlib.contextmanager
def capture() -> Generator[_RingBuffer]:
    """Capture stdout/stderr written within this context (and within threads it spawns via
    `asyncio.to_thread`), while still passing everything through to the real streams.

    Yields the buffer; call `.getvalue()` for the captured text."""
    buffer = _RingBuffer()
    token = _current.set(buffer)
    try:
        yield buffer
    finally:
        # Suppress an abandoned async generator to avoid polluting stderr
        with contextlib.suppress(ValueError):
            _current.reset(token)


def clip(text: str, limit: int = MAX_CAPTURED_CHARS) -> str:
    """Truncate an error string, keeping the head and tail (the useful parts)."""
    if not text or len(text) <= limit:
        return text
    keep = limit // 2
    omitted = len(text) - 2 * keep
    return f"{text[:keep]}\n...[{omitted} characters truncated]...\n{text[-keep:]}"
