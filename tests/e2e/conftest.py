"""E2E test fixtures: provision real endpoints, configure SDK, clean up."""

import json
import logging
import os
import urllib.request
from pathlib import Path

import pytest
import runpod

from tests.e2e.e2e_provisioner import load_test_cases, provision_endpoints

log = logging.getLogger(__name__)
REQUEST_TIMEOUT = 300  # seconds per job request

# Repo root: tests/e2e/conftest.py -> ../../
_REPO_ROOT = Path(__file__).resolve().parents[2]

_GRAPHQL_URL = "https://api.runpod.io/graphql"


def _graphql(api_key: str, query: str, variables: dict | None = None) -> dict:
    """Execute a Runpod GraphQL query."""
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        f"{_GRAPHQL_URL}?api_key={api_key}",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _delete_endpoints_by_name(api_key: str, names: list[str]) -> None:
    """Delete endpoints matching the given names via GraphQL API."""
    result = _graphql(api_key, """
        query { myself { endpoints { id name } } }
    """)
    all_endpoints = result.get("data", {}).get("myself", {}).get("endpoints", [])
    name_set = set(names)
    targets = [ep for ep in all_endpoints if ep.get("name") in name_set]

    if not targets:
        log.warning("No matching endpoints found for names: %s", names)
        return

    for ep in targets:
        try:
            resp = _graphql(
                api_key,
                "mutation($id: String!) { deleteEndpoint(id: $id) }",
                {"id": ep["id"]},
            )
            if "errors" in resp:
                log.warning("Failed to delete %s (%s): %s", ep["name"], ep["id"], resp["errors"])
            else:
                log.info("Deleted endpoint %s (%s)", ep["name"], ep["id"])
        except Exception:
            log.exception("Error deleting endpoint %s (%s)", ep["name"], ep["id"])


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

    # Delete provisioned endpoints via GraphQL API directly.
    # flash undeploy relies on .runpod/resources.pkl which doesn't exist in CI.
    api_key = os.environ.get("RUNPOD_API_KEY", "")
    endpoint_names = [ep.name for ep in eps.values()]
    log.info("Cleaning up %d provisioned endpoints: %s", len(endpoint_names), endpoint_names)
    if api_key and endpoint_names:
        _delete_endpoints_by_name(api_key, endpoint_names)
