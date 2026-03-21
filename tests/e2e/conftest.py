"""E2E test fixtures: provision real endpoints, configure SDK, clean up."""

import logging
import os
import subprocess

import pytest
import runpod

from tests.e2e.e2e_provisioner import load_test_cases, provision_endpoints

log = logging.getLogger(__name__)
REQUEST_TIMEOUT = 300  # seconds per job request


@pytest.fixture(scope="session", autouse=True)
def verify_local_runpod():
    """Fail fast if the local runpod-python is not installed."""
    log.info("runpod version=%s path=%s", runpod.__version__, runpod.__file__)
    if "runpod-python" not in runpod.__file__:
        pytest.fail(
            f"Expected local runpod-python but got {runpod.__file__}. "
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

    # Undeploy all provisioned endpoints via CLI
    log.info("Cleaning up %d provisioned endpoints via flash undeploy", len(eps))
    try:
        result = subprocess.run(
            ["flash", "undeploy", "--all", "--force"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        log.info("flash undeploy stdout: %s", result.stdout)
        if result.returncode != 0:
            log.warning("flash undeploy failed (rc=%d): %s", result.returncode, result.stderr)
    except Exception:
        log.exception("Failed to run flash undeploy")


@pytest.fixture(scope="session")
def api_key():
    """Return the RUNPOD_API_KEY."""
    return os.environ.get("RUNPOD_API_KEY", "")
