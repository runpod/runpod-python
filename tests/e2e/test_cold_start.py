import asyncio
import os
import signal
import time

import pytest

from tests.e2e.conftest import wait_for_ready

pytestmark = pytest.mark.cold_start

COLD_START_PORT = 8199
COLD_START_THRESHOLD = 60  # seconds


@pytest.mark.asyncio
async def test_cold_start_under_threshold():
    """flash run reaches health within 60 seconds."""
    fixture_dir = os.path.join(
        os.path.dirname(__file__), "fixtures", "all_in_one"
    )
    proc = await asyncio.create_subprocess_exec(
        "flash", "run", "--port", str(COLD_START_PORT),
        cwd=fixture_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    start = time.monotonic()
    try:
        await wait_for_ready(
            f"http://localhost:{COLD_START_PORT}/docs",
            timeout=COLD_START_THRESHOLD,
            poll_interval=0.5,
        )
        elapsed = time.monotonic() - start
        assert elapsed < COLD_START_THRESHOLD, (
            f"Cold start took {elapsed:.1f}s, expected < {COLD_START_THRESHOLD}s"
        )
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(proc.wait(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
