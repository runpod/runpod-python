import asyncio
import os
import signal
import time

import httpx
import pytest
import pytest_asyncio


async def _wait_for_ready(url: str, timeout: float = 60) -> None:
    """Poll a URL until it returns 200 or timeout is reached."""
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return
            except (httpx.ConnectError, httpx.ConnectTimeout):
                pass
            await asyncio.sleep(1)
    raise TimeoutError(f"Server not ready at {url} after {timeout}s")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def verify_local_runpod():
    """Fail fast if the local runpod-python is not installed."""
    import runpod

    assert "runpod-python" in runpod.__file__, (
        f"Expected local runpod-python but got {runpod.__file__}. "
        "Run: pip install -e . --force-reinstall --no-deps"
    )


@pytest_asyncio.fixture(scope="session")
async def flash_server(verify_local_runpod):
    """Start flash run dev server, yield base URL, teardown with SIGINT."""
    fixture_dir = os.path.join(
        os.path.dirname(__file__), "fixtures", "all_in_one"
    )
    proc = await asyncio.create_subprocess_exec(
        "flash", "run", "--port", "8100",
        cwd=fixture_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        await _wait_for_ready("http://localhost:8100/docs", timeout=60)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        pytest.fail("flash run did not become ready within 60s")

    yield {"base_url": "http://localhost:8100", "process": proc}

    proc.send_signal(signal.SIGINT)
    try:
        await asyncio.wait_for(proc.wait(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


@pytest_asyncio.fixture
async def http_client():
    """Async HTTP client with extended timeout for remote dispatch."""
    async with httpx.AsyncClient(timeout=120) as client:
        yield client


@pytest.fixture
def require_api_key():
    """Skip test if RUNPOD_API_KEY is not set."""
    if not os.environ.get("RUNPOD_API_KEY"):
        pytest.skip("RUNPOD_API_KEY not set, skipping LB tests")
