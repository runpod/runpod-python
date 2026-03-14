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
