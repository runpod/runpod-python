import asyncio
import os
import signal
import time

import httpx
import pytest

pytestmark = pytest.mark.cold_start


async def _wait_for_ready(url: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.5)
    raise TimeoutError(f"Server not ready at {url} after {timeout}s")


@pytest.mark.asyncio
async def test_cold_start_under_threshold():
    """flash run reaches health within 60 seconds."""
    fixture_dir = os.path.join(
        os.path.dirname(__file__), "fixtures", "all_in_one"
    )
    proc = await asyncio.create_subprocess_exec(
        "flash", "run", "--port", "8101",
        cwd=fixture_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    start = time.monotonic()
    try:
        await _wait_for_ready("http://localhost:8101/docs", timeout=60)
        elapsed = time.monotonic() - start
        assert elapsed < 60, f"Cold start took {elapsed:.1f}s, expected < 60s"
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(proc.wait(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
