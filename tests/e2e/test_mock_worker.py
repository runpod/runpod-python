"""E2E tests against real Runpod serverless endpoints running mock-worker.

Submits all jobs concurrently across provisioned endpoints, then asserts
each result matches the expected output from tests.json.
"""

import asyncio
import json
import logging
from pathlib import Path

import pytest

from tests.e2e.e2e_provisioner import hardware_config_key

log = logging.getLogger(__name__)

TESTS_JSON = Path(__file__).parent / "tests.json"
REQUEST_TIMEOUT = 300  # seconds


def _load_test_cases():
    return json.loads(TESTS_JSON.read_text())


async def _run_single_case(test_case: dict, endpoints: dict, api_key: str) -> None:
    """Submit one job, wait for completion, and assert output."""
    test_id = test_case.get("id", "unknown")
    hw_key = hardware_config_key(test_case["hardwareConfig"])
    ep = endpoints[hw_key]

    log.info("[%s] Submitting job to endpoint=%s input=%s", test_id, ep.name, test_case["input"])
    job = await ep.run(test_case["input"])
    log.info("[%s] Job submitted: job_id=%s, waiting (timeout=%ds)", test_id, job.id, REQUEST_TIMEOUT)
    await job.wait(timeout=REQUEST_TIMEOUT)

    log.info(
        "[%s] Job completed: job_id=%s done=%s output=%s error=%s",
        test_id, job.id, job.done, job.output, job.error,
    )

    assert job.done, f"[{test_id}] Job {job.id} did not reach terminal status"
    assert job.error is None, f"[{test_id}] Job {job.id} failed: {job.error}"

    if "expected_output" in test_case:
        assert job.output == test_case["expected_output"], (
            f"[{test_id}] Expected {test_case['expected_output']}, got {job.output}"
        )


@pytest.mark.asyncio
async def test_mock_worker_jobs(endpoints, api_key):
    """Submit all test jobs concurrently and verify outputs."""
    test_cases = _load_test_cases()
    results = await asyncio.gather(
        *[_run_single_case(tc, endpoints, api_key) for tc in test_cases],
        return_exceptions=True,
    )

    failures = []
    for tc, result in zip(test_cases, results):
        if isinstance(result, Exception):
            failures.append(f"[{tc.get('id', '?')}] {result}")

    assert not failures, f"{len(failures)} job(s) failed:\n" + "\n".join(failures)
