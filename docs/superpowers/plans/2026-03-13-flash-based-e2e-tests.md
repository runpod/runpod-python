# Flash-Based E2E Tests Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the opaque CI-e2e.yml with flash-based e2e tests that validate runpod-python SDK behaviors against a real `flash run` dev server.

**Architecture:** Single flash project fixture with QB endpoints (sync, async, stateful) and one LB endpoint. Session-scoped async pytest fixture manages `flash run` subprocess lifecycle with SIGINT cleanup. Two-tier CI: QB tests on every PR (< 5 min), LB tests nightly.

**Tech Stack:** runpod-flash (flash CLI), pytest + pytest-asyncio (test framework), httpx (async HTTP client), asyncio subprocess management.

**Spec:** `docs/superpowers/specs/2026-03-13-flash-based-e2e-tests-design.md`

---

## File Structure

```
tests/e2e/                          # NEW directory
├── __init__.py                     # Package marker
├── conftest.py                     # Session fixtures: flash_server, http_client, verify_local_runpod
├── fixtures/
│   └── all_in_one/                 # Purpose-built flash project
│       ├── pyproject.toml          # Minimal flash project config
│       ├── sync_handler.py         # QB: sync function
│       ├── async_handler.py        # QB: async function
│       ├── stateful_handler.py     # QB: stateful function with typed params
│       └── lb_endpoint.py          # LB: HTTP POST route via PodTemplate
├── test_worker_handlers.py         # @pytest.mark.qb — sync, async handler tests
├── test_worker_state.py            # @pytest.mark.qb — state persistence tests
├── test_endpoint_client.py         # @pytest.mark.qb — SDK Endpoint client tests
├── test_async_endpoint.py          # @pytest.mark.qb — async SDK Endpoint client tests
├── test_lb_dispatch.py             # @pytest.mark.lb — LB remote dispatch tests
└── test_cold_start.py              # @pytest.mark.cold_start — startup benchmark

.github/workflows/CI-e2e.yml       # REPLACE existing file
.github/workflows/CI-e2e-nightly.yml  # NEW nightly workflow
pytest.ini                          # MODIFY — add markers
```

---

## Chunk 1: Fixture Project and Test Infrastructure

### Task 1: Create fixture project directory and pyproject.toml

**Files:**
- Create: `tests/e2e/__init__.py`
- Create: `tests/e2e/fixtures/all_in_one/pyproject.toml`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p tests/e2e/fixtures/all_in_one
touch tests/e2e/__init__.py
```

- [ ] **Step 2: Write pyproject.toml**

Create `tests/e2e/fixtures/all_in_one/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "e2e-test-fixture"
version = "0.1.0"
description = "Purpose-built fixture for runpod-python e2e tests"
requires-python = ">=3.11"
dependencies = [
    "runpod-flash",
]
```

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/__init__.py tests/e2e/fixtures/all_in_one/pyproject.toml
git commit -m "chore: scaffold e2e test directory and fixture project"
```

---

### Task 2: Create QB fixture handlers

**Files:**
- Create: `tests/e2e/fixtures/all_in_one/sync_handler.py`
- Create: `tests/e2e/fixtures/all_in_one/async_handler.py`
- Create: `tests/e2e/fixtures/all_in_one/stateful_handler.py`

- [ ] **Step 1: Write sync_handler.py**

Create `tests/e2e/fixtures/all_in_one/sync_handler.py`:

```python
from runpod_flash import Endpoint


@Endpoint(name="sync-worker", cpu="cpu3c-1-2")
def sync_handler(input_data: dict) -> dict:
    return {"input_received": input_data, "status": "ok"}
```

- [ ] **Step 2: Write async_handler.py**

Create `tests/e2e/fixtures/all_in_one/async_handler.py`:

```python
from runpod_flash import Endpoint


@Endpoint(name="async-worker", cpu="cpu3c-1-2")
async def async_handler(input_data: dict) -> dict:
    return {"input_received": input_data, "status": "ok"}
```

- [ ] **Step 3: Write stateful_handler.py**

Create `tests/e2e/fixtures/all_in_one/stateful_handler.py`:

```python
from typing import Optional

from runpod_flash import Endpoint

state = {}


@Endpoint(name="stateful-worker", cpu="cpu3c-1-2")
def stateful_handler(action: str, key: str, value: Optional[str] = None) -> dict:
    if action == "set":
        state[key] = value
        return {"stored": True}
    elif action == "get":
        return {"value": state.get(key)}
    return {"error": "unknown action"}
```

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/fixtures/all_in_one/sync_handler.py tests/e2e/fixtures/all_in_one/async_handler.py tests/e2e/fixtures/all_in_one/stateful_handler.py
git commit -m "feat: add QB fixture handlers for e2e tests"
```

---

### Task 3: Create LB fixture handler

**Files:**
- Create: `tests/e2e/fixtures/all_in_one/lb_endpoint.py`

- [ ] **Step 1: Write lb_endpoint.py**

Create `tests/e2e/fixtures/all_in_one/lb_endpoint.py`:

```python
import os

from runpod_flash import Endpoint, GpuType, PodTemplate

branch = os.environ.get("RUNPOD_PYTHON_BRANCH", "main")

template = PodTemplate(
    startScript=(
        f"pip install git+https://github.com/runpod/runpod-python@{branch} "
        f"--no-cache-dir && python3 -u /src/handler.py"
    ),
)

config = Endpoint(
    name="lb-worker",
    gpu=GpuType.NVIDIA_GEFORCE_RTX_4090,
    template=template,
)


@config.post("/echo")
async def echo(text: str) -> dict:
    return {"echoed": text}
```

- [ ] **Step 2: Commit**

```bash
git add tests/e2e/fixtures/all_in_one/lb_endpoint.py
git commit -m "feat: add LB fixture handler for e2e tests"
```

---

### Task 4: Add pytest markers to pytest.ini

**Files:**
- Modify: `pytest.ini`

- [ ] **Step 1: Add markers to pytest.ini**

The file currently contains:

```ini
[pytest]
addopts = --durations=10 --cov-config=.coveragerc --timeout=120 --timeout_method=thread --cov=runpod --cov-report=xml --cov-report=term-missing --cov-fail-under=90 -W error -p no:cacheprovider -p no:unraisableexception
python_files = tests.py test_*.py *_test.py
norecursedirs = venv *.egg-info .git build
asyncio_mode = auto
```

Append marker definitions after `asyncio_mode = auto`:

```ini
markers =
    qb: Queue-based tests (local execution, fast)
    lb: Load-balanced tests (remote provisioning, slow)
    cold_start: Cold start benchmark (starts own server)
```

The full file after editing:

```ini
[pytest]
addopts = --durations=10 --cov-config=.coveragerc --timeout=120 --timeout_method=thread --cov=runpod --cov-report=xml --cov-report=term-missing --cov-fail-under=90 -W error -p no:cacheprovider -p no:unraisableexception
python_files = tests.py test_*.py *_test.py
norecursedirs = venv *.egg-info .git build
asyncio_mode = auto
markers =
    qb: Queue-based tests (local execution, fast)
    lb: Load-balanced tests (remote provisioning, slow)
    cold_start: Cold start benchmark (starts own server)
```

- [ ] **Step 2: Verify markers are registered**

Run: `python -m pytest --markers | grep -E "qb|lb|cold_start"`

Expected: All three markers appear without warnings.

- [ ] **Step 3: Commit**

```bash
git add pytest.ini
git commit -m "chore: register e2e pytest markers (qb, lb, cold_start)"
```

---

### Task 5: Create conftest.py with server lifecycle fixtures

**Files:**
- Create: `tests/e2e/conftest.py`

- [ ] **Step 1: Write conftest.py**

Create `tests/e2e/conftest.py`:

```python
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
            except httpx.ConnectError:
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
    """Async HTTP client with 30s timeout for test requests."""
    async with httpx.AsyncClient(timeout=30) as client:
        yield client


@pytest.fixture
def require_api_key():
    """Skip test if RUNPOD_API_KEY is not set."""
    if not os.environ.get("RUNPOD_API_KEY"):
        pytest.skip("RUNPOD_API_KEY not set, skipping LB tests")
```

- [ ] **Step 2: Verify conftest loads without errors**

Run: `python -m pytest tests/e2e/ --collect-only 2>&1 | head -20`

Expected: No import errors. May show "no tests collected" since test files don't exist yet.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/conftest.py
git commit -m "feat: add e2e conftest with flash_server lifecycle fixture"
```

---

## Chunk 2: QB Test Files (Tier 1)

### Task 6: Write test_worker_handlers.py

**Files:**
- Create: `tests/e2e/test_worker_handlers.py`

- [ ] **Step 1: Write the test file**

Create `tests/e2e/test_worker_handlers.py`:

```python
import pytest

pytestmark = pytest.mark.qb


@pytest.mark.asyncio
async def test_sync_handler(flash_server, http_client):
    """Sync QB handler receives input and returns expected output."""
    url = f"{flash_server['base_url']}/sync_handler/runsync"
    resp = await http_client.post(url, json={"input": {"prompt": "hello"}})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["output"]["input_received"] == {"prompt": "hello"}
    assert body["output"]["status"] == "ok"


@pytest.mark.asyncio
async def test_async_handler(flash_server, http_client):
    """Async QB handler receives input and returns expected output."""
    url = f"{flash_server['base_url']}/async_handler/runsync"
    resp = await http_client.post(url, json={"input": {"prompt": "hello"}})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["output"]["input_received"] == {"prompt": "hello"}
    assert body["output"]["status"] == "ok"


@pytest.mark.asyncio
async def test_handler_error_propagation(flash_server, http_client):
    """Malformed input surfaces an error response."""
    url = f"{flash_server['base_url']}/sync_handler/runsync"
    resp = await http_client.post(url, json={"input": None})

    assert resp.status_code in (400, 422, 500)
```

- [ ] **Step 2: Verify test collects**

Run: `python -m pytest tests/e2e/test_worker_handlers.py --collect-only`

Expected: 3 tests collected.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_worker_handlers.py
git commit -m "feat: add e2e tests for sync and async QB handlers"
```

---

### Task 7: Write test_worker_state.py

**Files:**
- Create: `tests/e2e/test_worker_state.py`

- [ ] **Step 1: Write the test file**

Create `tests/e2e/test_worker_state.py`:

```python
import uuid

import pytest

pytestmark = pytest.mark.qb


@pytest.mark.asyncio
async def test_state_persists_across_calls(flash_server, http_client):
    """Setting a value via one call is retrievable in the next call."""
    url = f"{flash_server['base_url']}/stateful_handler/runsync"
    test_key = f"test-{uuid.uuid4().hex[:8]}"

    set_resp = await http_client.post(
        url,
        json={"input": {"action": "set", "key": test_key, "value": "hello"}},
    )
    assert set_resp.status_code == 200
    assert set_resp.json()["output"]["stored"] is True

    get_resp = await http_client.post(
        url,
        json={"input": {"action": "get", "key": test_key}},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["output"]["value"] == "hello"


@pytest.mark.asyncio
async def test_state_independent_keys(flash_server, http_client):
    """Multiple keys persist independently."""
    url = f"{flash_server['base_url']}/stateful_handler/runsync"
    key_a = f"key-a-{uuid.uuid4().hex[:8]}"
    key_b = f"key-b-{uuid.uuid4().hex[:8]}"

    await http_client.post(
        url,
        json={"input": {"action": "set", "key": key_a, "value": "alpha"}},
    )
    await http_client.post(
        url,
        json={"input": {"action": "set", "key": key_b, "value": "beta"}},
    )

    resp_a = await http_client.post(
        url,
        json={"input": {"action": "get", "key": key_a}},
    )
    resp_b = await http_client.post(
        url,
        json={"input": {"action": "get", "key": key_b}},
    )

    assert resp_a.json()["output"]["value"] == "alpha"
    assert resp_b.json()["output"]["value"] == "beta"
```

- [ ] **Step 2: Verify test collects**

Run: `python -m pytest tests/e2e/test_worker_state.py --collect-only`

Expected: 2 tests collected.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_worker_state.py
git commit -m "feat: add e2e tests for stateful worker persistence"
```

---

### Task 8: Write test_endpoint_client.py

**Files:**
- Create: `tests/e2e/test_endpoint_client.py`

- [ ] **Step 1: Write the test file**

The SDK's `runpod.Endpoint` constructs URLs as `{runpod.endpoint_url_base}/{endpoint_id}/runsync`. Flash serves QB routes at `/{file_prefix}/runsync`. Setting `runpod.endpoint_url_base = "http://localhost:8100"` and using `endpoint_id = "sync_handler"` makes the SDK hit the flash dev server.

Create `tests/e2e/test_endpoint_client.py`:

```python
import pytest
import runpod

pytestmark = pytest.mark.qb


@pytest.fixture(autouse=True)
def _patch_runpod_base_url(flash_server):
    """Point the SDK Endpoint client at the local flash server."""
    original = runpod.endpoint_url_base
    runpod.endpoint_url_base = flash_server["base_url"]
    yield
    runpod.endpoint_url_base = original


@pytest.mark.asyncio
async def test_run_sync(flash_server):
    """SDK Endpoint.run_sync() submits a job and gets the result."""
    endpoint = runpod.Endpoint("sync_handler")
    result = endpoint.run_sync({"input_data": {"prompt": "test"}})

    assert result["input_received"] == {"prompt": "test"}
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_run_async_poll(flash_server):
    """SDK Endpoint.run() submits async job, poll status, get output."""
    endpoint = runpod.Endpoint("sync_handler")
    run_request = endpoint.run({"input_data": {"prompt": "poll-test"}})

    status = run_request.status()
    assert status in ("IN_QUEUE", "IN_PROGRESS", "COMPLETED")

    output = run_request.output(timeout=30)
    assert output["input_received"] == {"prompt": "poll-test"}
    assert output["status"] == "ok"


@pytest.mark.asyncio
async def test_run_sync_error(flash_server):
    """SDK Endpoint.run_sync() surfaces handler errors."""
    endpoint = runpod.Endpoint("sync_handler")

    with pytest.raises(Exception):
        endpoint.run_sync(None)
```

**Note:** The exact `run_sync`/`run` argument format and error behavior may need adjustment during implementation based on how the SDK client serializes the request body. The `run_sync` method wraps the argument in `{"input": ...}` before sending. The `run` method returns a `Job` object with `.status()` and `.output()` methods. Verify by reading `runpod/endpoint/runner.py`.

- [ ] **Step 2: Verify test collects**

Run: `python -m pytest tests/e2e/test_endpoint_client.py --collect-only`

Expected: 3 tests collected.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_endpoint_client.py
git commit -m "feat: add e2e tests for SDK Endpoint client round-trip"
```

---

### Task 9: Write test_async_endpoint.py

**Files:**
- Create: `tests/e2e/test_async_endpoint.py`

- [ ] **Step 1: Write the test file**

The SDK has an async endpoint client at `runpod.endpoint.asyncio`. This test validates the async variant.

Create `tests/e2e/test_async_endpoint.py`:

```python
import pytest
import runpod
from runpod.endpoint.asyncio import asyncio_runner

pytestmark = pytest.mark.qb


@pytest.fixture(autouse=True)
def _patch_runpod_base_url(flash_server):
    """Point the SDK Endpoint client at the local flash server."""
    original = runpod.endpoint_url_base
    runpod.endpoint_url_base = flash_server["base_url"]
    yield
    runpod.endpoint_url_base = original


@pytest.mark.asyncio
async def test_async_run(flash_server):
    """Async SDK client submits a job and polls for output."""
    endpoint = asyncio_runner.Job("async_handler")
    # Submit job asynchronously
    await endpoint.run({"input_data": {"prompt": "async-test"}})

    status = await endpoint.status()
    assert status in ("IN_QUEUE", "IN_PROGRESS", "COMPLETED")

    output = await endpoint.output(timeout=30)
    assert output["input_received"] == {"prompt": "async-test"}
    assert output["status"] == "ok"


@pytest.mark.asyncio
async def test_async_run_sync_fallback(flash_server):
    """Sync SDK Endpoint works against async handler endpoint."""
    endpoint = runpod.Endpoint("async_handler")
    result = endpoint.run_sync({"input_data": {"prompt": "sync-to-async"}})

    assert result["input_received"] == {"prompt": "sync-to-async"}
    assert result["status"] == "ok"
```

**Note:** The async client API in `runpod/endpoint/asyncio/asyncio_runner.py` may differ from the pattern above. During implementation, read the actual class to determine the correct method signatures. The key point is testing the async code path, not just calling sync methods.

- [ ] **Step 2: Verify test collects**

Run: `python -m pytest tests/e2e/test_async_endpoint.py --collect-only`

Expected: 2 tests collected.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_async_endpoint.py
git commit -m "feat: add e2e tests for async SDK Endpoint client"
```

---

### Task 10: Write test_cold_start.py

**Files:**
- Create: `tests/e2e/test_cold_start.py`

- [ ] **Step 1: Write the test file**

This test starts its own `flash run` process on port 8101 (separate from the session fixture on 8100) and measures time to health.

Create `tests/e2e/test_cold_start.py`:

```python
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
```

- [ ] **Step 2: Verify test collects**

Run: `python -m pytest tests/e2e/test_cold_start.py --collect-only`

Expected: 1 test collected.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_cold_start.py
git commit -m "feat: add e2e cold start benchmark test"
```

---

## Chunk 3: LB Tests and CI Workflows

### Task 11: Write test_lb_dispatch.py

**Files:**
- Create: `tests/e2e/test_lb_dispatch.py`

- [ ] **Step 1: Write the test file**

Create `tests/e2e/test_lb_dispatch.py`:

```python
import os

import pytest
import runpod

pytestmark = pytest.mark.lb


@pytest.mark.asyncio
async def test_lb_echo(flash_server, http_client, require_api_key):
    """LB endpoint echoes text through remote dispatch."""
    url = f"{flash_server['base_url']}/echo"
    resp = await http_client.post(url, json={"text": "hello"})

    assert resp.status_code == 200
    assert resp.json()["echoed"] == "hello"


@pytest.mark.asyncio
async def test_lb_uses_target_branch(flash_server, http_client, require_api_key):
    """Provisioned LB endpoint runs the target runpod-python branch."""
    expected_branch = os.environ.get("RUNPOD_PYTHON_BRANCH", "main")

    # The echo endpoint returns a response; if it works, the startScript
    # successfully installed the target branch. A version mismatch or
    # install failure would cause 500 errors, not a successful echo.
    url = f"{flash_server['base_url']}/echo"
    resp = await http_client.post(url, json={"text": expected_branch})

    assert resp.status_code == 200
    assert resp.json()["echoed"] == expected_branch
```

**Note:** LB tests require `RUNPOD_API_KEY` in the environment and a provisioned GPU pod. The `require_api_key` fixture skips if the key is absent. The `test_lb_uses_target_branch` test validates that the `PodTemplate(startScript=...)` pattern works — if the pip install of the target branch fails, the handler would not start and requests would fail with 500. A more robust version check could be added if the SDK exposes a version endpoint.

- [ ] **Step 2: Verify test collects**

Run: `python -m pytest tests/e2e/test_lb_dispatch.py --collect-only`

Expected: 2 tests collected.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_lb_dispatch.py
git commit -m "feat: add e2e tests for LB remote dispatch"
```

---

### Task 12: Create CI-e2e.yml (replaces existing)

**Files:**
- Replace: `.github/workflows/CI-e2e.yml`

- [ ] **Step 1: Read existing CI-e2e.yml to understand what we're replacing**

Run: `cat .github/workflows/CI-e2e.yml`

Document the existing structure for reference.

- [ ] **Step 2: Write the new CI-e2e.yml**

Replace `.github/workflows/CI-e2e.yml` with:

```yaml
name: CI-e2e
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

jobs:
  e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v3
        with:
          version: "latest"

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          uv venv
          source .venv/bin/activate
          pip install runpod-flash
          pip install -e . --force-reinstall --no-deps
          python -c "import runpod; print(f'runpod: {runpod.__version__} from {runpod.__file__}')"
          pip install pytest pytest-asyncio pytest-timeout httpx

      - name: Run QB e2e tests
        run: |
          source .venv/bin/activate
          pytest tests/e2e/ -v -m "qb or cold_start" -p no:xdist --timeout=300 -o "addopts="

      - name: Cleanup flash resources
        if: always()
        run: |
          source .venv/bin/activate
          pkill -f "flash run" || true
          cd tests/e2e/fixtures/all_in_one
          flash undeploy --force 2>/dev/null || true
```

- [ ] **Step 3: Validate YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/CI-e2e.yml'))"`

Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/CI-e2e.yml
git commit -m "feat: replace CI-e2e.yml with flash-based QB e2e tests"
```

---

### Task 13: Create CI-e2e-nightly.yml

**Files:**
- Create: `.github/workflows/CI-e2e-nightly.yml`

- [ ] **Step 1: Write the nightly workflow**

Create `.github/workflows/CI-e2e-nightly.yml`:

```yaml
name: CI-e2e-nightly
on:
  schedule:
    - cron: '0 6 * * *'  # 6 AM UTC daily
  workflow_dispatch:

jobs:
  e2e-full:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v3
        with:
          version: "latest"

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          uv venv
          source .venv/bin/activate
          pip install runpod-flash
          pip install -e . --force-reinstall --no-deps
          python -c "import runpod; print(f'runpod: {runpod.__version__} from {runpod.__file__}')"
          pip install pytest pytest-asyncio pytest-timeout httpx

      - name: Run full e2e tests
        run: |
          source .venv/bin/activate
          pytest tests/e2e/ -v -p no:xdist --timeout=600 -o "addopts="
        env:
          RUNPOD_API_KEY: ${{ secrets.RUNPOD_API_KEY }}
          # Nightly always tests main. Branch-specific LB testing
          # requires manual workflow_dispatch with a branch override.
          RUNPOD_PYTHON_BRANCH: main

      - name: Cleanup flash resources
        if: always()
        run: |
          source .venv/bin/activate
          pkill -f "flash run" || true
          cd tests/e2e/fixtures/all_in_one
          flash undeploy --force 2>/dev/null || true
```

- [ ] **Step 2: Validate YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/CI-e2e-nightly.yml'))"`

Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/CI-e2e-nightly.yml
git commit -m "feat: add nightly CI workflow for full e2e suite including LB"
```

---

## Chunk 4: Local Validation and Final Commit

### Task 14: Smoke test the QB suite locally

This task validates the entire implementation works end-to-end before pushing.

**Prerequisites:** `runpod-flash` installed in the venv, local runpod-python installed via `pip install -e .`.

- [ ] **Step 1: Install flash and local SDK**

```bash
source .venv/bin/activate
pip install runpod-flash
pip install -e . --force-reinstall --no-deps
python -c "import runpod; print(f'runpod: {runpod.__version__} from {runpod.__file__}')"
```

Verify output shows the local path containing `runpod-python`.

- [ ] **Step 2: Verify flash discovers fixture handlers**

```bash
cd tests/e2e/fixtures/all_in_one
flash run --port 8100 &
sleep 10
curl -s http://localhost:8100/docs | head -20
kill %1
cd -
```

Expected: The `/docs` endpoint returns HTML (Swagger UI). If it fails, check flash output for discovery errors.

- [ ] **Step 3: Run QB tests**

```bash
python -m pytest tests/e2e/ -v -m "qb" -p no:xdist --timeout=120 --no-header -rN --override-ini="addopts=" 2>&1
```

**Important:** The `--override-ini="addopts="` clears the default `addopts` from `pytest.ini` which includes `--cov=runpod` and `--cov-fail-under=90` — these would interfere with e2e tests that don't cover the main package.

Expected: All QB tests pass. If a test fails, check:
- URL pattern: verify `flash run` generates routes matching `/{file_prefix}/runsync`
- Request format: verify the handler receives the `input` contents correctly
- Response format: verify the envelope structure matches `{"id": ..., "status": "COMPLETED", "output": ...}`

- [ ] **Step 4: Run cold start test**

```bash
python -m pytest tests/e2e/test_cold_start.py -v -p no:xdist --timeout=120 --no-header -rN --override-ini="addopts=" 2>&1
```

Expected: Cold start test passes (server ready within 60s).

- [ ] **Step 5: Verify LB test skips without API key**

```bash
unset RUNPOD_API_KEY
python -m pytest tests/e2e/test_lb_dispatch.py -v -p no:xdist --timeout=30 --no-header -rN --override-ini="addopts=" 2>&1
```

Expected: Test is skipped with message "RUNPOD_API_KEY not set, skipping LB tests".

- [ ] **Step 6: Final commit with all files**

If any adjustments were needed during smoke testing, stage the specific changed files and commit:

```bash
git add <specific changed files>
git commit -m "fix: adjust e2e tests based on smoke test findings"
```

---

### Task 15: Update branch CLAUDE.md with progress

**Files:**
- Modify: `CLAUDE.md` (worktree root)

- [ ] **Step 1: Update CLAUDE.md**

Update the branch context in the worktree CLAUDE.md to reflect completed work:

```markdown
## Branch Context

**Purpose:** Replace opaque CI-e2e.yml with flash-based e2e tests

**Status:** Implementation complete, pending PR review

**Dependencies:** runpod-flash (PyPI)

## Branch-Specific Notes

- QB tests (sync, async, stateful handlers, endpoint client, cold start) run on every PR
- LB tests (remote dispatch) run nightly only
- Tests use `flash run` dev server with async subprocess management
- SIGINT cleanup triggers flash's built-in undeploy-on-cancel

## Key Files

- `tests/e2e/conftest.py` — flash_server session fixture
- `tests/e2e/fixtures/all_in_one/` — purpose-built flash project
- `.github/workflows/CI-e2e.yml` — PR workflow (QB only, 5 min)
- `.github/workflows/CI-e2e-nightly.yml` — nightly workflow (full suite, 15 min)
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update branch CLAUDE.md with e2e implementation context"
```
