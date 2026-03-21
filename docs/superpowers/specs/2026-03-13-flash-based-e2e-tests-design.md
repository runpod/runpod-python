# Flash-Based E2E Tests for runpod-python

**Date:** 2026-03-13
**Branch:** `build/flash-based-e2e-tests`
**Status:** Implemented, pending PR review

## Problem

The existing e2e test infrastructure (`CI-e2e.yml`) depends on:

- `runpod-workers/mock-worker` — an external repo maintained by a former employee
- `runpod/runpod-test-runner@v2.1.0` — an opaque GitHub Action with unknown internals
- Docker Hub credentials and `RUNPOD_API_KEY` secrets tied to an unknown account
- 20-minute CI timeout with no visibility into what is actually validated

The tests are unmaintainable, untrusted, and tied to infrastructure we do not control.

## Solution

Replace the existing e2e suite with tests that use `runpod-flash` to execute real SDK behaviors against a local `flash run` dev server. This validates the full SDK pipeline — handler execution, job lifecycle, state persistence, and endpoint client — without depending on external repos or opaque actions.

## Architecture

### Single Server, All Routes

One purpose-built flash project containing all fixture endpoints. A single `flash run` process serves every test. Tests hit different routes on the same server.

**Why single server:** Fits the 5-minute CI budget. Each `flash run` startup + teardown costs ~45s. Running multiple servers would consume the entire budget on lifecycle alone.

**Trade-off accepted:** Tests share a server. A crashing handler could affect other tests. This is acceptable because a crash is a real bug worth catching. State tests use unique keys per test run to avoid cross-test contamination.

### Two-Tier Test Strategy: QB (CI) and LB (Nightly)

**Tier 1 — QB tests (run on every PR, < 5 minutes):**
QB routes execute locally in-process via `flash run`. No remote provisioning needed. These validate handler execution, state persistence, endpoint client, and cold start.

**Tier 2 — LB tests (nightly schedule, ~10 minutes):**
LB routes provision real serverless endpoints on Runpod. GPU pod startup + `pip install` from git takes 2-5 minutes, which exceeds the PR CI budget. These run on a nightly schedule and validate remote dispatch, cross-worker communication, and the `PodTemplate(startScript=...)` SDK version injection pattern.

### SDK Version Targeting

The e2e tests must validate the runpod-python branch under test, not the PyPI release bundled with flash.

- **QB routes (local process):** `flash run` executes handlers in-process. The venv has the local runpod-python installed via `pip install -e . --force-reinstall --no-deps` after `pip install runpod-flash`. The editable install overrides the transitive dependency. A version guard fixture verifies this at test startup.

- **LB routes (remote containers):** `flash run` provisions real serverless endpoints for LB routes. Those containers ship with a pinned `runpod` from PyPI. The fixture overrides this via `PodTemplate(startScript=...)` which installs the target branch at container startup before running the handler.

```python
from runpod_flash import Endpoint, GpuType, PodTemplate

branch = os.environ.get("RUNPOD_PYTHON_BRANCH", "main")

template = PodTemplate(
    startScript=(
        f'pip install git+https://github.com/runpod/runpod-python@{branch} '
        f'--no-cache-dir && python3 -u /src/handler.py'
    ),
)
```

CI passes the branch name:

```yaml
env:
  RUNPOD_PYTHON_BRANCH: ${{ github.head_ref || github.ref_name }}
```

## URL Routing and Request/Response Format

`flash run` auto-discovers all `.py` files in the project directory (excluding `.flash/`, `.venv/`, `__pycache__/`, `__init__.py`). No config file is needed for discovery.

### QB Route URL Pattern

For a file with a single callable:
```
POST /{file_prefix}/runsync
```

For a file with multiple callables:
```
POST /{file_prefix}/{function_name}/runsync
```

Example: `sync_handler.py` with one handler generates `POST /sync_handler/runsync`.

### Request Body Format

```json
{
  "input": {
    "param1": "value1",
    "param2": "value2"
  }
}
```

### Response Body Format

```json
{
  "id": "uuid-string",
  "status": "COMPLETED",
  "output": {
    "input_received": {"param1": "value1"},
    "status": "ok"
  }
}
```

### LB Route URL Pattern

Custom HTTP paths as defined by `@config.post("/echo")` etc.

## Fixture Project

```
tests/e2e/fixtures/all_in_one/
├── sync_handler.py        # QB: sync function, returns dict
├── async_handler.py       # QB: async function, returns dict
├── stateful_handler.py    # QB: reads/writes worker state between calls
├── lb_endpoint.py         # LB: HTTP POST route via PodTemplate
└── pyproject.toml         # Minimal flash project config
```

Each file defines one `@Endpoint` with the simplest possible implementation — just enough to prove the SDK behavior works. No ML models, no external dependencies.

**Note:** Generator handlers are not supported by `flash run`'s dev server. If generator support is added later, a `generator_handler.py` fixture can be added.

### pyproject.toml

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

### sync_handler.py

The `@Endpoint(...)` decorator is used directly on the function (not as `config.handler`). Flash's `_call_with_body` helper maps the `input` field from the request body to the function's first parameter.

```python
from runpod_flash import Endpoint


@Endpoint(name="sync-worker", cpu="cpu3c-1-2")
def sync_handler(input_data: dict) -> dict:
    return {"input_received": input_data, "status": "ok"}
```

### async_handler.py

```python
from runpod_flash import Endpoint


@Endpoint(name="async-worker", cpu="cpu3c-1-2")
async def async_handler(input_data: dict) -> dict:
    return {"input_received": input_data, "status": "ok"}
```

### stateful_handler.py

Uses typed parameters instead of a `job` dict, since flash maps request body fields directly to function kwargs.

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

### lb_endpoint.py

```python
import os

from runpod_flash import Endpoint, GpuType, PodTemplate

branch = os.environ.get("RUNPOD_PYTHON_BRANCH", "main")

template = PodTemplate(
    startScript=(
        f'pip install git+https://github.com/runpod/runpod-python@{branch} '
        f'--no-cache-dir && python3 -u /src/handler.py'
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

## Test Framework

### Pytest Markers

Defined in `pyproject.toml` or `pytest.ini`:

```ini
[tool:pytest]
markers =
    qb: Queue-based tests (local execution, fast)
    lb: Load-balanced tests (remote provisioning, slow)
    cold_start: Cold start benchmark (starts own server)
```

### Server Lifecycle (conftest.py)

Session-scoped async fixture manages the `flash run` subprocess:

```python
import asyncio
import os
import signal
import time

import httpx
import pytest
import pytest_asyncio


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
    fixture_dir = os.path.join(
        os.path.dirname(__file__), "fixtures", "all_in_one"
    )
    proc = await asyncio.create_subprocess_exec(
        "flash", "run", "--port", "8100",
        cwd=fixture_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    await _wait_for_ready("http://localhost:8100/docs", timeout=60)

    yield {"base_url": "http://localhost:8100", "process": proc}

    # Graceful shutdown — SIGINT triggers flash's undeploy-on-cancel
    proc.send_signal(signal.SIGINT)
    try:
        await asyncio.wait_for(proc.wait(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


@pytest_asyncio.fixture
async def http_client():
    async with httpx.AsyncClient(timeout=30) as client:
        yield client
```

### Test Files

```
tests/e2e/
├── conftest.py                # flash_server fixture + helpers
├── fixtures/
│   └── all_in_one/            # Purpose-built flash project
│       ├── sync_handler.py
│       ├── async_handler.py
│       ├── stateful_handler.py
│       ├── lb_endpoint.py
│       └── pyproject.toml
├── test_worker_handlers.py    # @pytest.mark.qb — sync, async execution
├── test_worker_state.py       # @pytest.mark.qb — state persistence
├── test_endpoint_client.py    # @pytest.mark.qb — SDK client round-trip
├── test_async_endpoint.py     # @pytest.mark.qb — async SDK client
├── test_lb_dispatch.py        # @pytest.mark.lb — LB remote dispatch
└── test_cold_start.py         # @pytest.mark.cold_start — startup benchmark
```

### test_worker_handlers.py

Validates that the SDK's handler execution pipeline works end-to-end.

- **test_sync_handler** — `POST /sync_handler/runsync` with `{"input": {"prompt": "hello"}}`, verify `output.input_received == {"prompt": "hello"}`
- **test_async_handler** — `POST /async_handler/runsync` with same pattern, verify async handler produces identical result
- **test_handler_error_propagation** — `POST /sync_handler/runsync` with `{"input": null}`, verify response contains error information (status 400 or 500)

### test_worker_state.py

Validates state persistence between sequential handler calls. Tests run sequentially (not parallel) to avoid state races.

- **test_state_persists_across_calls** — POST `{"input": {"action": "set", "key": "<uuid>", "value": "test"}}`, then POST `{"input": {"action": "get", "key": "<uuid>"}}`, verify value returned
- **test_state_independent_keys** — set two UUID-keyed values, verify both persist independently

UUID keys per test run prevent cross-test contamination when the session-scoped server is shared.

### test_endpoint_client.py

Validates the SDK's `runpod.Endpoint` client against the real server. The SDK client uses a module-level `runpod.endpoint_url_base` variable to construct URLs as `{endpoint_url_base}/{endpoint_id}/runsync`. Flash generates QB routes at `/{file_prefix}/runsync`. Setting `runpod.endpoint_url_base = "http://localhost:8100"` with `endpoint_id = "sync_handler"` produces `http://localhost:8100/sync_handler/runsync`, which matches the flash dev server.

```python
import runpod

# Point SDK at local flash server
runpod.endpoint_url_base = "http://localhost:8100"
endpoint = runpod.Endpoint("sync_handler")
```

- **test_run_sync** — `Endpoint.run_sync()` submits job to sync-worker, gets result
- **test_run_async_poll** — `Endpoint.run()` submits job, `Job.status()` polls, `Job.output()` gets result
- **test_run_sync_error** — `Endpoint.run_sync()` submits malformed input, verify SDK surfaces the error (raises exception or returns error object)

### test_async_endpoint.py

Same as endpoint client but using the async SDK variant. Tests async job submission, polling, and result retrieval.

### test_lb_dispatch.py

Marked `@pytest.mark.lb`. Validates LB route remote dispatch through the flash server.

- **test_lb_echo** — `POST /echo` with `{"text": "hello"}`, verify `{"echoed": "hello"}` returned
- **test_lb_uses_target_branch** — verify the provisioned endpoint is running the target runpod-python branch (can check via a version endpoint or response header if available)

**Note:** LB tests require `RUNPOD_API_KEY` and a provisioned GPU pod. They are excluded from PR CI and run on a nightly schedule.

### test_cold_start.py

Measures startup latency. Starts its own `flash run` process (not the session fixture) and measures time to health.

- **test_cold_start_under_threshold** — `flash run` on port 8101 reaches health check in under 60s
- Manages its own process lifecycle with SIGINT teardown
- Uses a different port (8101) to avoid conflict with the session fixture

## CI Workflows

### CI-e2e.yml (PR — QB tests only)

Replaces the existing `CI-e2e.yml`:

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
          pytest tests/e2e/ -v -m "qb or cold_start" --timeout=300

      - name: Cleanup flash resources
        if: always()
        run: |
          source .venv/bin/activate
          pkill -f "flash run" || true
          cd tests/e2e/fixtures/all_in_one
          flash undeploy --force 2>/dev/null || true
```

### CI-e2e-nightly.yml (Nightly — full suite including LB)

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
          pytest tests/e2e/ -v --timeout=600
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

## Cleanup Strategy

Three layers of defense against resource leaks:

1. **SIGINT (normal path)** — fixture teardown sends SIGINT. Flash's built-in undeploy-on-cancel decommissions provisioned endpoints. Wait up to 30s for process exit.

2. **SIGKILL (timeout path)** — if flash hangs during undeploy, SIGKILL the process after 30s. Log a warning that resources may have leaked.

3. **CI post-step (safety net)** — `if: always()` step kills lingering flash processes and runs `flash undeploy --force` to clean up any leaked resources.

## Test Transformation Map

From the existing test suite, these tests have flash-based e2e counterparts:

| Existing Test | Classification | E2E Counterpart |
|---|---|---|
| `test_serverless/test_worker.py` | TRANSFORM | `test_worker_handlers.py` |
| `test_serverless/test_integration_worker_state.py` | TRANSFORM | `test_worker_state.py` |
| `test_endpoint/test_runner.py` | HYBRID | `test_endpoint_client.py` |
| `test_endpoint/test_asyncio_runner.py` | HYBRID | `test_async_endpoint.py` |
| `test_performance/test_cold_start.py` | HYBRID | `test_cold_start.py` |

The remaining 63 test files stay as unit tests — they test isolated functions, query generation, CLI parsing, and module exports where mocks are appropriate.

## Local Development

### Running QB tests locally (no API key needed)

```bash
cd runpod-python
pip install runpod-flash
pip install -e . --force-reinstall --no-deps
pytest tests/e2e/ -v -m "qb or cold_start"
```

The fixture manages `flash run` automatically. No manual server startup needed. SIGINT cleanup handles teardown.

### Running LB tests locally (requires API key)

```bash
export RUNPOD_API_KEY="your-key"
export RUNPOD_PYTHON_BRANCH="build/flash-based-e2e-tests"
pytest tests/e2e/ -v -m lb --timeout=600
```

LB tests provision real GPU endpoints. Expect 2-5 minutes for pod startup. The cleanup fixture and post-test `flash undeploy --force` handle teardown.

### Running the full suite

```bash
export RUNPOD_API_KEY="your-key"
pytest tests/e2e/ -v --timeout=600
```

### Skipping LB tests when no API key is present

LB test fixtures should skip gracefully if `RUNPOD_API_KEY` is not set:

```python
@pytest.fixture
def require_api_key():
    if not os.environ.get("RUNPOD_API_KEY"):
        pytest.skip("RUNPOD_API_KEY not set, skipping LB tests")
```

## Dependencies

New dev dependencies for e2e tests:

- `runpod-flash` — flash CLI and runtime (installed separately, not in pyproject.toml dev deps, to avoid circular dependency)
- `httpx` — async HTTP client for test assertions
- `pytest-asyncio` — async test support (already a dev dependency)
- `pytest-timeout` — per-test timeout enforcement (already a dev dependency, but explicitly installed in CI since we use `--no-deps`)

## Test Execution Constraints

- **No pytest-xdist for e2e tests** — tests share a session-scoped server. Parallel workers would each try to start their own server. Run with `-p no:xdist` if xdist is installed globally.
- **State tests run sequentially** — `test_worker_state.py` tests depend on call ordering. Use UUID keys to avoid interference from other tests running concurrently against the same server.
- **Cold start test uses port 8101** — avoids conflict with the session fixture on port 8100.

## Time Budget

### PR CI (QB + cold start only)

| Phase | Estimated Time |
|---|---|
| `pip install` | ~30s |
| `flash run` startup (QB only, no provisioning) | ~15s |
| QB test execution (4 files) | ~60s |
| Cold start test (own server on 8101) | ~75s |
| Teardown (SIGINT) | ~10s |
| Buffer | ~70s |
| **Total** | **~4.5 minutes** |

### Nightly (full suite including LB)

| Phase | Estimated Time |
|---|---|
| `pip install` | ~30s |
| `flash run` startup + LB provisioning | ~3-5 min |
| Full test execution (6 files) | ~120s |
| Teardown (SIGINT + undeploy) | ~60s |
| Buffer | ~120s |
| **Total** | **~10-12 minutes** |
