import asyncio
import os
import signal
import time

import httpx
import pytest
import pytest_asyncio
import runpod

FLASH_SERVER_PORT = 8100
SERVER_READY_TIMEOUT = 60  # seconds
TEARDOWN_TIMEOUT = 30  # seconds
HTTP_CLIENT_TIMEOUT = 120  # seconds


async def wait_for_ready(url: str, timeout: float = SERVER_READY_TIMEOUT, poll_interval: float = 1.0) -> None:
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
            await asyncio.sleep(poll_interval)
    raise TimeoutError(f"Server not ready at {url} after {timeout}s")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def verify_local_runpod():
    """Fail fast if the local runpod-python is not installed."""
    if "runpod-python" not in runpod.__file__:
        pytest.fail(
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
        "flash", "run", "--port", str(FLASH_SERVER_PORT),
        cwd=fixture_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    base_url = f"http://localhost:{FLASH_SERVER_PORT}"
    try:
        await wait_for_ready(f"{base_url}/docs", timeout=SERVER_READY_TIMEOUT)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        pytest.fail(f"flash run did not become ready within {SERVER_READY_TIMEOUT}s")

    yield {"base_url": base_url, "process": proc}

    proc.send_signal(signal.SIGINT)
    try:
        await asyncio.wait_for(proc.wait(), timeout=TEARDOWN_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


@pytest_asyncio.fixture
async def http_client():
    """Async HTTP client with extended timeout for remote dispatch."""
    async with httpx.AsyncClient(timeout=HTTP_CLIENT_TIMEOUT) as client:
        yield client


@pytest.fixture
def require_api_key():
    """Skip test if RUNPOD_API_KEY is not set."""
    if not os.environ.get("RUNPOD_API_KEY"):
        pytest.skip("RUNPOD_API_KEY not set")


@pytest.fixture(autouse=True)
def _patch_runpod_globals(flash_server):
    """Point the SDK Endpoint client at the local flash server and set API key."""
    original_url = runpod.endpoint_url_base
    original_key = runpod.api_key
    runpod.endpoint_url_base = flash_server["base_url"]
    runpod.api_key = os.environ.get("RUNPOD_API_KEY", "test-key")
    yield
    runpod.endpoint_url_base = original_url
    runpod.api_key = original_key
