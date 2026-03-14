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
