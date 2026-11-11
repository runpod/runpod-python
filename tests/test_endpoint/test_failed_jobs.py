"""a job that ends in a non-COMPLETED final state raises JobError."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

import runpod
from runpod.endpoint.asyncio.asyncio_runner import Job as AsyncJob
from runpod.endpoint.runner import Endpoint, Job
from runpod.error import JobError

UNSUCCESSFUL_FINAL_STATES = ["FAILED", "TIMED_OUT", "CANCELLED"]


@pytest.fixture
def endpoint():
    runpod.api_key = "MOCK_API_KEY"
    return Endpoint("ENDPOINT_ID")


@pytest.mark.parametrize("status", UNSUCCESSFUL_FINAL_STATES)
def test_run_sync_raises_with_status_and_error(endpoint, status):
    response = {"id": "job-1", "status": status, "error": "handler blew up"}

    with patch.object(runpod.endpoint.runner.RunPodClient, "_request") as request:
        request.return_value = response
        with pytest.raises(JobError) as caught:
            endpoint.run_sync({"prompt": "hi"})

    assert caught.value.job_id == "job-1"
    assert caught.value.status == status
    assert caught.value.error == "handler blew up"
    assert "handler blew up" in str(caught.value)


def test_run_sync_raises_when_polled_job_fails(endpoint):
    responses = [
        {"id": "job-1", "status": "IN_QUEUE"},
        {"id": "job-1", "status": "FAILED", "error": "out of memory"},
    ]

    with (
        patch.object(
            runpod.endpoint.runner.RunPodClient, "_request", side_effect=responses
        ),
        patch("runpod.endpoint.runner.time.sleep"),
    ):
        with pytest.raises(JobError, match="out of memory"):
            endpoint.run_sync({"prompt": "hi"}, timeout=5)


def test_run_sync_still_returns_none_output_of_a_completed_job(endpoint):
    with patch.object(runpod.endpoint.runner.RunPodClient, "_request") as request:
        request.return_value = {"id": "job-1", "status": "COMPLETED"}
        assert endpoint.run_sync({"prompt": "hi"}) is None


def test_job_output_raises_on_every_call_after_failure():
    client = Mock()
    client.get.return_value = {"status": "FAILED", "error": "bad input"}
    job = Job("ENDPOINT_ID", "job-1", client)

    for _ in range(2):
        with pytest.raises(JobError, match="bad input"):
            job.output()


def test_job_error_without_error_field_still_names_the_status():
    client = Mock()
    client.get.return_value = {"status": "TIMED_OUT"}
    job = Job("ENDPOINT_ID", "job-1", client)

    with pytest.raises(JobError, match="TIMED_OUT") as caught:
        job.output()

    assert caught.value.error is None


async def test_async_job_output_raises_on_failure():
    response = AsyncMock()
    response.json.return_value = {"status": "FAILED", "error": "bad input"}
    session = AsyncMock()
    session.get.return_value = response
    job = AsyncJob("ENDPOINT_ID", "job-1", session, headers={})

    with pytest.raises(JobError, match="bad input") as caught:
        await job.output(timeout=3)

    assert caught.value.status == "FAILED"
