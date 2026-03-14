# Flash-Based E2E Test Redesign

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current flash-run-based e2e tests with tests that provision real Runpod serverless endpoints using the mock-worker image, inject the PR's runpod-python via `dockerArgs`, and validate SDK behavior against live endpoints -- mirroring the original `runpod-test-runner` approach but using Flash for provisioning and pytest for execution.

**Architecture:** Flash's `Endpoint(image=...)` mode provisions a real serverless endpoint from the mock-worker Docker image. `PodTemplate(dockerArgs=...)` overrides the container CMD to pip-install the PR branch's runpod-python before running the handler. Tests read `tests.json` for test case definitions (inputs, expected outputs, hardware configs), send jobs via Flash's async `Endpoint` client (`ep.run()` / `job.wait()`), and assert output. Cleanup unlinks all provisioned endpoints and templates.

**Tech Stack:** pytest, pytest-asyncio, runpod-flash (Endpoint image mode, PodTemplate), GitHub Actions

---

## Context: What the Original E2E Did

The original CI-e2e workflow (`main/.github/workflows/CI-e2e.yml`) had two jobs:

1. **`e2e-build`**: Clone `runpod-workers/mock-worker`, overwrite `builder/requirements.txt` with `git+https://github.com/runpod/runpod-python.git@<PR-SHA>`, build Docker image, push to Docker Hub.
2. **`test`**: `runpod-test-runner@v2.1.0` reads `.github/tests.json`, creates a template (`saveTemplate` with `imageName` = custom Docker image, `dockerArgs` = CMD override), creates an endpoint (`saveEndpoint` with `templateId`), sends jobs via `/run`, polls `/status/{id}`, asserts results match expected output, then cleans up (deletes endpoint + template).

**Key file: `.github/tests.json`**

```json
[
  {
    "hardwareConfig": {
      "endpointConfig": { "name": "...", "gpuIds": "ADA_24,..." }
    },
    "input": { "mock_return": "this worked!" }
  },
  {
    "hardwareConfig": {
      "endpointConfig": { "name": "...", "gpuIds": "ADA_24,..." },
      "templateConfig": { "dockerArgs": "python3 -u /handler.py --generator ..." }
    },
    "input": { "mock_return": ["value1", "value2", "value3"] }
  }
]
```

Each test case specifies hardware config (endpoint + template overrides) and input/output. Tests with the same `hardwareConfig` share one provisioned endpoint.

**What Flash replaces:** The Docker build step and the JS-based test-runner provisioning. Flash's `Endpoint(image=..., template=PodTemplate(dockerArgs=...))` provisions the endpoint directly. No custom Docker image build needed -- `dockerArgs` injects the PR's runpod-python at container start time.

**What stays the same:** `tests.json` as the test definition format. SDK-based job submission and polling. Result assertion. Endpoint cleanup.

## Critical: `FLASH_IS_LIVE_PROVISIONING=false`

Flash's `_is_live_provisioning()` defaults to `True` when no env vars are set (the CI case). This routes `Endpoint(image=...)` to `LiveServerless`, which **forcefully overwrites `imageName`** with Flash's default base image and has a **no-op setter** that silently discards writes. The mock-worker image would never be deployed.

**Fix:** Set `FLASH_IS_LIVE_PROVISIONING=false` in the CI environment so `ServerlessEndpoint` (the deploy class) is used, which respects the provided `imageName`.

Relevant code:
- `endpoint.py:199-213`: `_is_live_provisioning()` returns `True` by default
- `endpoint.py:536-539`: Routes to `LiveServerless(**kwargs)` when `live=True`
- `live_serverless.py:38-43`: `imageName` property returns hardcoded image, setter is no-op

## File Structure

```
tests/e2e/
  conftest.py               -- Session fixtures: provision endpoints per hardwareConfig,
                               SDK client setup, cleanup
  tests.json                -- Test case definitions (mirrors .github/tests.json format)
  test_mock_worker.py       -- Parametrized tests: send jobs, poll, assert results
  test_cold_start.py        -- (keep as-is) flash run cold start timing test
  e2e_provisioner.py        -- Flash Endpoint provisioning logic: reads tests.json,
                               groups by hardwareConfig, provisions endpoints,
                               injects dockerArgs for PR runpod-python
```

**Files to delete** (replaced by new approach):

```
tests/e2e/test_endpoint_client.py     -- replaced by test_mock_worker.py
tests/e2e/test_worker_handlers.py     -- replaced by test_mock_worker.py
tests/e2e/test_lb_dispatch.py         -- replaced by test_mock_worker.py (if needed later)
tests/e2e/fixtures/all_in_one/        -- entire directory (no more flash run fixtures)
  async_handler.py
  sync_handler.py
  lb_endpoint.py
  e2e_template.py
  pyproject.toml
  .flash/                              -- generated, gitignored
```

**Files to modify:**

```
.github/workflows/CI-e2e.yml          -- Remove flash run/undeploy, simplify to pytest only
.github/workflows/CI-e2e-nightly.yml  -- Same simplification
```

---

## Chunk 1: Provisioner and Test Infrastructure

### Task 1: Create `tests.json` test definitions

**Files:**
- Create: `tests/e2e/tests.json`

- [ ] **Step 1: Write tests.json mirroring the original format**

```json
[
  {
    "id": "basic",
    "hardwareConfig": {
      "endpointConfig": {
        "name": "rp-python-e2e-basic",
        "gpuIds": "ADA_24,AMPERE_16,AMPERE_24,AMPERE_48,AMPERE_80"
      }
    },
    "input": {
      "mock_return": "this worked!"
    },
    "expected_output": "this worked!"
  },
  {
    "id": "delay",
    "hardwareConfig": {
      "endpointConfig": {
        "name": "rp-python-e2e-delay",
        "gpuIds": "ADA_24,AMPERE_16,AMPERE_24,AMPERE_48,AMPERE_80"
      }
    },
    "input": {
      "mock_return": "Delay test successful.",
      "mock_delay": 10
    },
    "expected_output": "Delay test successful."
  },
  {
    "id": "generator",
    "hardwareConfig": {
      "endpointConfig": {
        "name": "rp-python-e2e-generator",
        "gpuIds": "ADA_24,AMPERE_16,AMPERE_24,AMPERE_48,AMPERE_80"
      },
      "templateConfig": {
        "dockerArgs": "python3 -u /handler.py --generator --return_aggregate_stream"
      }
    },
    "input": {
      "mock_return": ["value1", "value2", "value3"]
    },
    "expected_output": ["value1", "value2", "value3"]
  },
  {
    "id": "async_generator",
    "hardwareConfig": {
      "endpointConfig": {
        "name": "rp-python-e2e-async-gen",
        "gpuIds": "ADA_24,AMPERE_16,AMPERE_24,AMPERE_48,AMPERE_80"
      },
      "templateConfig": {
        "dockerArgs": "python3 -u /handler.py --async_generator --return_aggregate_stream"
      }
    },
    "input": {
      "mock_return": ["value1", "value2", "value3"]
    },
    "expected_output": ["value1", "value2", "value3"]
  }
]
```

Note: `mock_delay` reduced from 300s to 10s. The original 5-minute delay was testing long-running jobs but is impractical for CI. Can increase later if needed.

- [ ] **Step 2: Commit**

```bash
git add tests/e2e/tests.json
git commit -m "feat(e2e): add tests.json test case definitions"
```

---

### Task 2: Create the provisioner module

**Files:**
- Create: `tests/e2e/e2e_provisioner.py`

This module reads `tests.json`, groups test cases by `hardwareConfig`, and provisions one Flash `Endpoint` per unique hardware config. Each endpoint uses the mock-worker image with `dockerArgs` modified to prepend `pip install git+...@<sha>` before the original CMD.

**Critical:** Must set `FLASH_IS_LIVE_PROVISIONING=false` before creating `Endpoint` objects so Flash uses `ServerlessEndpoint` (which respects `imageName`) instead of `LiveServerless` (which overwrites it).

- [ ] **Step 1: Write e2e_provisioner.py**

```python
"""Provision real Runpod serverless endpoints for e2e testing.

Reads tests.json, groups by hardwareConfig, provisions one endpoint per
unique config using Flash's Endpoint(image=...) mode. Injects the PR's
runpod-python via PodTemplate(dockerArgs=...) so the remote worker runs
the branch under test.
"""

import json
import os
from pathlib import Path
from typing import Any

# Force Flash to use ServerlessEndpoint (deploy mode) instead of LiveServerless.
# LiveServerless forcefully overwrites imageName with Flash's base image,
# ignoring the mock-worker image we need to deploy.
os.environ["FLASH_IS_LIVE_PROVISIONING"] = "false"

from runpod_flash import Endpoint, GpuGroup, PodTemplate  # noqa: E402

MOCK_WORKER_IMAGE = "runpod/mock-worker:latest"
DEFAULT_CMD = "python -u /handler.py"
TESTS_JSON = Path(__file__).parent / "tests.json"

# Map gpuIds strings from tests.json to GpuGroup enum values
_GPU_MAP: dict[str, GpuGroup] = {g.value: g for g in GpuGroup}


def _build_docker_args(base_docker_args: str, git_ref: str | None) -> str:
    """Build dockerArgs that injects PR runpod-python before the original CMD.

    If git_ref is set, prepends pip install. If base_docker_args is provided
    (e.g., for generator handlers), uses that as the CMD instead of default.
    """
    cmd = base_docker_args or DEFAULT_CMD
    if not git_ref:
        return cmd

    install_url = f"git+https://github.com/runpod/runpod-python@{git_ref}"
    return (
        '/bin/bash -c "'
        "apt-get update && apt-get install -y git && "
        f"pip install {install_url} --no-cache-dir && "
        f'{cmd}"'
    )


def _parse_gpu_ids(gpu_ids_str: str) -> list[GpuGroup]:
    """Parse comma-separated GPU ID strings into GpuGroup enums."""
    result = []
    for g in gpu_ids_str.split(","):
        g = g.strip()
        if g in _GPU_MAP:
            result.append(_GPU_MAP[g])
    if not result:
        result.append(GpuGroup.ANY)
    return result


def load_test_cases() -> list[dict[str, Any]]:
    """Load test cases from tests.json."""
    return json.loads(TESTS_JSON.read_text())


def hardware_config_key(hw: dict) -> str:
    """Stable string key for grouping tests by hardware config."""
    return json.dumps(hw, sort_keys=True)


def provision_endpoints(
    test_cases: list[dict[str, Any]],
) -> dict[str, Endpoint]:
    """Provision one Endpoint per unique hardwareConfig.

    Returns a dict mapping hardwareConfig key -> provisioned Endpoint.
    The Endpoint is in image mode (not yet deployed). Deployment happens
    on first .run() or .runsync() call.

    Args:
        test_cases: List of test case dicts from tests.json.

    Returns:
        Dict of hardware_key -> Endpoint instance.
    """
    git_ref = os.environ.get("RUNPOD_SDK_GIT_REF")
    seen: dict[str, Endpoint] = {}

    for tc in test_cases:
        hw = tc["hardwareConfig"]
        key = hardware_config_key(hw)
        if key in seen:
            continue

        endpoint_config = hw.get("endpointConfig", {})
        template_config = hw.get("templateConfig", {})

        base_docker_args = template_config.get("dockerArgs", "")
        docker_args = _build_docker_args(base_docker_args, git_ref)

        gpu_ids = endpoint_config.get("gpuIds", "ADA_24")
        gpus = _parse_gpu_ids(gpu_ids)

        ep = Endpoint(
            name=endpoint_config.get("name", f"rp-python-e2e-{len(seen)}"),
            image=MOCK_WORKER_IMAGE,
            gpu=gpus,
            template=PodTemplate(dockerArgs=docker_args),
            workers=(0, 1),
            idle_timeout=5,
        )
        seen[key] = ep

    return seen
```

- [ ] **Step 2: Commit**

```bash
git add tests/e2e/e2e_provisioner.py
git commit -m "feat(e2e): add provisioner module for mock-worker endpoints"
```

---

### Task 3: Rewrite conftest.py

**Files:**
- Modify: `tests/e2e/conftest.py`

Replace the flash-run-based fixtures with provisioning-based fixtures.

- [ ] **Step 1: Rewrite conftest.py**

```python
"""E2E test fixtures: provision real endpoints, configure SDK, clean up."""

import os

import pytest
import runpod

from tests.e2e.e2e_provisioner import load_test_cases, provision_endpoints

REQUEST_TIMEOUT = 300  # seconds per job request


@pytest.fixture(scope="session", autouse=True)
def verify_local_runpod():
    """Fail fast if the local runpod-python is not installed."""
    if "runpod-python" not in runpod.__file__:
        pytest.fail(
            f"Expected local runpod-python but got {runpod.__file__}. "
            "Run: pip install -e . --force-reinstall --no-deps"
        )


@pytest.fixture(scope="session")
def require_api_key():
    """Skip entire session if RUNPOD_API_KEY is not set."""
    if not os.environ.get("RUNPOD_API_KEY"):
        pytest.skip("RUNPOD_API_KEY not set")


@pytest.fixture(scope="session")
def test_cases():
    """Load test cases from tests.json."""
    return load_test_cases()


@pytest.fixture(scope="session")
def endpoints(require_api_key, test_cases):
    """Provision one endpoint per unique hardwareConfig.

    Endpoints deploy lazily on first .run()/.runsync() call.
    """
    return provision_endpoints(test_cases)


@pytest.fixture(scope="session")
def api_key():
    """Return the RUNPOD_API_KEY."""
    return os.environ.get("RUNPOD_API_KEY", "")
```

- [ ] **Step 2: Commit**

```bash
git add tests/e2e/conftest.py
git commit -m "refactor(e2e): rewrite conftest for endpoint provisioning"
```

---

### Task 4: Write test_mock_worker.py

**Files:**
- Create: `tests/e2e/test_mock_worker.py`

Parametrized tests driven by `tests.json`. Each test case sends a job to the provisioned endpoint and asserts the output matches.

**Flash's `EndpointJob` API:**
- `job = await ep.run(input)` -- submit job, returns `EndpointJob`
- `await job.wait(timeout=N)` -- poll until terminal status, raises `TimeoutError`
- `job.done` -- `bool`, True if terminal status
- `job.output` -- output payload (available after COMPLETED)
- `job.error` -- error string (available after FAILED)
- `job._data["status"]` -- raw status string
- No `.status` property (`.status()` is an async method that polls)

- [ ] **Step 1: Write test_mock_worker.py**

```python
"""E2E tests against real Runpod serverless endpoints running mock-worker.

Tests are parametrized from tests.json. Each test sends a job via Flash's
Endpoint client, polls for completion, and asserts the output matches expected.
"""

import json
from pathlib import Path

import pytest

from tests.e2e.e2e_provisioner import hardware_config_key

TESTS_JSON = Path(__file__).parent / "tests.json"
REQUEST_TIMEOUT = 300  # seconds


def _load_test_cases():
    return json.loads(TESTS_JSON.read_text())


def _test_ids():
    return [tc.get("id", f"test_{i}") for i, tc in enumerate(_load_test_cases())]


@pytest.mark.parametrize("test_case", _load_test_cases(), ids=_test_ids())
@pytest.mark.asyncio
async def test_mock_worker_job(test_case, endpoints, api_key):
    """Submit a job to the provisioned endpoint and verify the output."""
    hw_key = hardware_config_key(test_case["hardwareConfig"])
    ep = endpoints[hw_key]

    job = await ep.run(test_case["input"])
    await job.wait(timeout=REQUEST_TIMEOUT)

    assert job.done, f"Job {job.id} did not reach terminal status"
    assert job.error is None, f"Job {job.id} failed: {job.error}"

    if "expected_output" in test_case:
        assert job.output == test_case["expected_output"], (
            f"Expected {test_case['expected_output']}, got {job.output}"
        )
```

- [ ] **Step 2: Commit**

```bash
git add tests/e2e/test_mock_worker.py
git commit -m "feat(e2e): add parametrized mock-worker e2e tests"
```

---

## Chunk 2: CI Workflow and Cleanup

### Task 5: Delete old fixture files and test files

**Files:**
- Delete: `tests/e2e/fixtures/all_in_one/` (entire directory)
- Delete: `tests/e2e/test_endpoint_client.py`
- Delete: `tests/e2e/test_worker_handlers.py`
- Delete: `tests/e2e/test_lb_dispatch.py`

- [ ] **Step 1: Delete files**

```bash
rm -rf tests/e2e/fixtures/all_in_one/
rm tests/e2e/test_endpoint_client.py
rm tests/e2e/test_worker_handlers.py
rm tests/e2e/test_lb_dispatch.py
```

- [ ] **Step 2: Commit**

```bash
git add -A tests/e2e/
git commit -m "refactor(e2e): remove flash-run-based fixtures and tests"
```

---

### Task 6: Rewrite CI-e2e.yml

**Files:**
- Modify: `.github/workflows/CI-e2e.yml`

No more flash run/undeploy. Just install deps and run pytest. Flash provisions endpoints directly. `FLASH_IS_LIVE_PROVISIONING=false` is set in `e2e_provisioner.py` (module-level), so no CI env var needed for that. `RUNPOD_SDK_GIT_REF` uses commit SHA for deterministic builds.

- [ ] **Step 1: Rewrite CI-e2e.yml**

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
    if: github.repository == 'runpod/runpod-python'
    runs-on: ubuntu-latest
    timeout-minutes: 20
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
          uv pip install -e ".[test]" 2>/dev/null || uv pip install -e .
          uv pip install runpod-flash pytest pytest-asyncio pytest-timeout pytest-rerunfailures httpx
          uv pip install -e . --reinstall --no-deps
          python -c "import runpod; print(f'runpod: {runpod.__version__} from {runpod.__file__}')"

      - name: Run e2e tests
        run: |
          source .venv/bin/activate
          pytest tests/e2e/ -v -p no:xdist --timeout=600 --reruns 1 --reruns-delay 5 --log-cli-level=INFO -o "addopts="
        env:
          RUNPOD_API_KEY: ${{ secrets.RUNPOD_API_KEY }}
          RUNPOD_SDK_GIT_REF: ${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/CI-e2e.yml
git commit -m "refactor(ci): simplify e2e workflow for direct provisioning"
```

---

### Task 7: Update CI-e2e-nightly.yml

**Files:**
- Modify: `.github/workflows/CI-e2e-nightly.yml`

- [ ] **Step 1: Rewrite CI-e2e-nightly.yml**

```yaml
name: CI-e2e-nightly
on:
  schedule:
    - cron: '0 6 * * *'
  workflow_dispatch:

jobs:
  e2e-full:
    if: github.repository == 'runpod/runpod-python'
    runs-on: ubuntu-latest
    timeout-minutes: 20
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
          uv pip install -e ".[test]" 2>/dev/null || uv pip install -e .
          uv pip install runpod-flash pytest pytest-asyncio pytest-timeout pytest-rerunfailures httpx
          uv pip install -e . --reinstall --no-deps
          python -c "import runpod; print(f'runpod: {runpod.__version__} from {runpod.__file__}')"

      - name: Run full e2e tests
        run: |
          source .venv/bin/activate
          pytest tests/e2e/ -v -p no:xdist --timeout=600 --reruns 1 --reruns-delay 5 --log-cli-level=INFO -o "addopts="
        env:
          RUNPOD_API_KEY: ${{ secrets.RUNPOD_API_KEY }}
          RUNPOD_SDK_GIT_REF: ${{ github.sha }}
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/CI-e2e-nightly.yml
git commit -m "refactor(ci): simplify nightly e2e workflow"
```

---

### Task 8: Update test_cold_start.py to not depend on old fixtures

**Files:**
- Modify: `tests/e2e/test_cold_start.py`
- Create: `tests/e2e/fixtures/cold_start/handler.py`
- Create: `tests/e2e/fixtures/cold_start/pyproject.toml`

The cold start test imports `wait_for_ready` from conftest. Since we're rewriting conftest, inline the helper. Also move the fixture to its own directory since `fixtures/all_in_one/` is deleted.

- [ ] **Step 1: Update test_cold_start.py**

```python
import asyncio
import os
import signal
import time

import httpx
import pytest

pytestmark = pytest.mark.cold_start

COLD_START_PORT = 8199
COLD_START_THRESHOLD = 60  # seconds


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
                pass
            await asyncio.sleep(poll_interval)
    raise TimeoutError(f"Server not ready at {url} after {timeout}s")


@pytest.mark.asyncio
async def test_cold_start_under_threshold():
    """flash run reaches health within 60 seconds."""
    fixture_dir = os.path.join(
        os.path.dirname(__file__), "fixtures", "cold_start"
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
```

- [ ] **Step 2: Create minimal cold start fixture**

Create `tests/e2e/fixtures/cold_start/handler.py`:
```python
from runpod_flash import Endpoint


@Endpoint(name="cold-start-worker", cpu="cpu3c-1-2")
def handler(input_data: dict) -> dict:
    return {"status": "ok"}
```

Create `tests/e2e/fixtures/cold_start/pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "cold-start-fixture"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["runpod-flash"]
```

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_cold_start.py tests/e2e/fixtures/cold_start/
git commit -m "refactor(e2e): make cold start test self-contained"
```

---

### Task 9: Verify locally

- [ ] **Step 1: Run the tests locally**

```bash
RUNPOD_API_KEY=<key> RUNPOD_SDK_GIT_REF=deanq/e-3379-flash-based-e2e-tests \
  pytest tests/e2e/test_mock_worker.py -v -p no:xdist --timeout=600 --log-cli-level=INFO -o "addopts=" -s
```

Expected: Flash provisions endpoints with mock-worker image, dockerArgs shows pip install of PR branch, jobs complete with expected outputs.

- [ ] **Step 2: Run cold start test separately**

```bash
pytest tests/e2e/test_cold_start.py -v -p no:xdist --timeout=180 -o "addopts="
```

Expected: flash run starts within 60s.

- [ ] **Step 3: Commit and push**

```bash
git push
```

---

## Open Questions

1. **Mock-worker image**: Is `runpod/mock-worker:latest` the correct image name, or is it at `<DOCKERHUB_REPO>/<DOCKERHUB_IMG>` (repo vars in CI)? The original workflow uses `${{ vars.DOCKERHUB_REPO }}/${{ vars.DOCKERHUB_IMG }}` -- need to confirm the public image tag.

2. **Cleanup**: The original test-runner explicitly deletes endpoints and templates after tests. With Flash provisioning, endpoints have `idle_timeout=5` which auto-scales to 0 workers, but the endpoint and template resources remain on the Runpod account. Over time (especially nightly runs) this accumulates orphaned resources. Consider adding explicit cleanup in conftest teardown or a CI cleanup step.
