"""E2E test fixtures: provision real endpoints, configure SDK, clean up."""

import logging
import os
import subprocess
from pathlib import Path

import pytest
import runpod

from tests.e2e.e2e_provisioner import load_test_cases, provision_endpoints

log = logging.getLogger(__name__)
REQUEST_TIMEOUT = 300  # seconds per job request

# Repo root: tests/e2e/conftest.py -> ../../
_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def verify_local_runpod():
    """Fail fast if the local runpod-python is not installed."""
    log.info("runpod version=%s path=%s", runpod.__version__, runpod.__file__)
    runpod_path = Path(runpod.__file__).resolve()
    if not runpod_path.is_relative_to(_REPO_ROOT):
        pytest.fail(
            f"Expected runpod installed from {_REPO_ROOT} but got {runpod_path}. "
            "Run: pip install -e . --force-reinstall --no-deps"
        )


@pytest.fixture(scope="session")
def require_api_key():
    """Skip entire session if RUNPOD_API_KEY is not set."""
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        pytest.skip("RUNPOD_API_KEY not set")
    log.info("RUNPOD_API_KEY is set (length=%d)", len(key))


@pytest.fixture(scope="session")
def test_cases():
    """Load test cases from tests.json."""
    cases = load_test_cases()
    log.info("Loaded %d test cases: %s", len(cases), [c.get("id") for c in cases])
    return cases


@pytest.fixture(scope="session")
def endpoints(require_api_key, test_cases):
    """Provision one endpoint per unique hardwareConfig.

    Endpoints deploy lazily on first .run()/.runsync() call.
    """
    eps = provision_endpoints(test_cases)
    for key, ep in eps.items():
        log.info("Endpoint ready: name=%s image=%s template.dockerArgs=%s", ep.name, ep.image, ep.template.dockerArgs if ep.template else "N/A")
    yield eps

    # Undeploy only the endpoints provisioned by this test run.
    # Uses by-name undeploy to avoid tearing down unrelated endpoints
    # sharing the same API key (parallel CI runs, developer endpoints).
    endpoint_names = [ep.name for ep in eps.values()]
    log.info("Cleaning up %d provisioned endpoints: %s", len(endpoint_names), endpoint_names)
    for name in endpoint_names:
        try:
            result = subprocess.run(
                ["flash", "undeploy", name, "--force"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                log.info("Undeployed %s", name)
            else:
                log.warning("flash undeploy %s failed (rc=%d): %s", name, result.returncode, result.stderr)
        except Exception:
            log.exception("Failed to undeploy %s", name)
