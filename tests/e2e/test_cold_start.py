import asyncio
import os
import signal
import tempfile
import time

import httpx
import pytest

pytestmark = pytest.mark.cold_start

COLD_START_PORT = 8199
COLD_START_THRESHOLD = 60  # seconds
LOG_TAIL_LINES = 50  # lines of output to include on failure


async def _wait_for_ready(url: str, timeout: float, poll_interval: float = 0.5) -> None:
    """Poll a URL until it returns 200 or timeout is reached."""
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return
            except (httpx.ConnectError, httpx.ConnectTimeout):
                # Expected while server is booting — retry until deadline.
                continue
            await asyncio.sleep(poll_interval)
    raise TimeoutError(f"Server not ready at {url} after {timeout}s")


def _tail(path: str, n: int = LOG_TAIL_LINES) -> str:
    """Return the last n lines of a file, or empty string if unreadable."""
    try:
        with open(path) as f:
            lines = f.readlines()
        return "".join(lines[-n:])
    except OSError:
        return ""


@pytest.mark.asyncio
async def test_cold_start_under_threshold():
    """flash run reaches health within 60 seconds."""
    fixture_dir = os.path.join(
        os.path.dirname(__file__), "fixtures", "cold_start"
    )
    log_file = tempfile.NamedTemporaryFile(
        prefix="flash-cold-start-", suffix=".log", delete=False, mode="w"
    )
    proc = await asyncio.create_subprocess_exec(
        "flash", "run", "--port", str(COLD_START_PORT),
        cwd=fixture_dir,
        stdout=log_file,
        stderr=asyncio.subprocess.STDOUT,
    )

    start = time.monotonic()
    try:
        await _wait_for_ready(
            f"http://localhost:{COLD_START_PORT}/docs",
            timeout=COLD_START_THRESHOLD,
        )
        elapsed = time.monotonic() - start
        assert elapsed < COLD_START_THRESHOLD, (
            f"Cold start took {elapsed:.1f}s, expected < {COLD_START_THRESHOLD}s"
            f"\n--- flash run output (last {LOG_TAIL_LINES} lines) ---\n"
            f"{_tail(log_file.name)}"
        )
    except (TimeoutError, AssertionError):
        log_file.flush()
        raise AssertionError(
            f"Cold start failed (elapsed={time.monotonic() - start:.1f}s)"
            f"\n--- flash run output (last {LOG_TAIL_LINES} lines) ---\n"
            f"{_tail(log_file.name)}"
        )
    finally:
        log_file.close()
        if proc.returncode is None:
            proc.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(proc.wait(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        os.unlink(log_file.name)
