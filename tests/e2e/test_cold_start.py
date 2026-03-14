import asyncio
import os
import re
import signal
import time

import httpx
import pytest

pytestmark = pytest.mark.cold_start

COLD_START_PORT = 8199
COLD_START_THRESHOLD = 60  # seconds


async def _wait_for_ready(url: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return
            except (httpx.ConnectError, httpx.ConnectTimeout):
                pass
            await asyncio.sleep(0.5)
    raise TimeoutError(f"Server not ready at {url} after {timeout}s")


async def _read_actual_port(proc: asyncio.subprocess.Process, requested_port: int) -> int:
    """Read flash run stdout to find the actual port (flash may auto-increment)."""
    deadline = time.monotonic() + 10
    port = requested_port
    while time.monotonic() < deadline:
        line = await asyncio.wait_for(proc.stderr.readline(), timeout=5)
        text = line.decode().strip()
        if f"localhost:{requested_port}" in text:
            return requested_port
        match = re.search(r"localhost:(\d+)", text)
        if match:
            port = int(match.group(1))
            return port
        if "Visit http://" in text:
            match = re.search(r"localhost:(\d+)", text)
            if match:
                return int(match.group(1))
    return port


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
        await _wait_for_ready(
            f"http://localhost:{COLD_START_PORT}/docs",
            timeout=COLD_START_THRESHOLD,
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
