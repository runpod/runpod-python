"""tests for the shared Apps SDK queue job."""

from typing import Any, Callable, Dict, Optional

import pytest

import runpod
from runpod.apps.app import _clear_registry
from runpod.apps.errors import InvalidResourceError
from runpod.apps.job import Job


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


class FakeTarget:
    def __init__(self):
        self.calls = []

    async def job_status(self, job_id: str) -> Dict[str, Any]:
        self.calls.append(("status", job_id))
        return {"id": job_id, "status": "IN_PROGRESS", "workerId": "worker-1"}

    async def wait(
        self,
        job_data: Dict[str, Any],
        *,
        timeout: float,
        on_status: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Any:
        self.calls.append(("wait", job_data["id"], timeout))
        final = {
            "id": job_data["id"],
            "status": "COMPLETED",
            "output": {"answer": 42},
        }
        if on_status is not None:
            on_status(final)
        return final["output"]

    async def cancel_job(self, job_id: str) -> Dict[str, Any]:
        self.calls.append(("cancel", job_id))
        return {"id": job_id, "status": "CANCELLED"}

    async def retry_job(self, job_id: str) -> Dict[str, Any]:
        self.calls.append(("retry", job_id))
        return {"id": job_id, "status": "IN_QUEUE", "retries": 1}

    async def stream_job(self, job_id: str, *, timeout: float = 300.0):
        self.calls.append(("stream", job_id))
        for chunk in ("a", "b", "c"):
            yield chunk


def test_job_status_polls_target():
    target = FakeTarget()
    job = Job({"id": "job-1", "status": "IN_QUEUE"}, target)

    assert job.status() == "IN_PROGRESS"
    assert target.calls == [("status", "job-1")]


def test_job_status_skips_poll_once_terminal():
    target = FakeTarget()
    job = Job({"id": "job-1", "status": "COMPLETED"}, target)

    assert job.status() == "COMPLETED"
    assert target.calls == []


def test_job_result_reaches_terminal_state():
    target = FakeTarget()
    job = Job({"id": "job-1", "status": "IN_QUEUE"}, target)

    assert job.result(timeout=12) == {"answer": 42}
    assert job.done is True
    assert job.status() == "COMPLETED"
    # the wait carried timeout through; terminal status needed no extra poll
    assert target.calls == [("wait", "job-1", 12)]


def test_job_output_aliases_result():
    target = FakeTarget()
    job = Job({"id": "job-1", "status": "IN_QUEUE"}, target)

    assert job.output() == {"answer": 42}


def test_job_cancel_and_retry_update_state():
    target = FakeTarget()
    job = Job({"id": "job-1", "status": "IN_PROGRESS"}, target)

    assert job.cancel() is job
    assert job.done is True
    assert job.retry() is job
    assert job.done is False
    assert target.calls == [("cancel", "job-1"), ("retry", "job-1")]


async def test_job_async_forms():
    target = FakeTarget()
    job = Job({"id": "job-1", "status": "IN_QUEUE"}, target)

    assert await job.status.aio() == "IN_PROGRESS"
    assert await job.result.aio() == {"answer": 42}
    assert await job.cancel.aio() is job
    assert await job.retry.aio() is job


def test_job_stream_sync():
    target = FakeTarget()
    job = Job({"id": "job-1", "status": "IN_QUEUE"}, target)

    assert list(job.stream()) == ["a", "b", "c"]
    assert target.calls == [("stream", "job-1")]


async def test_job_stream_async():
    target = FakeTarget()
    job = Job({"id": "job-1", "status": "IN_QUEUE"}, target)

    chunks = [chunk async for chunk in job.stream.aio()]
    assert chunks == ["a", "b", "c"]


def test_job_repr():
    job = Job({"id": "job-1", "status": "IN_QUEUE"}, FakeTarget())
    assert repr(job) == "Job(id='job-1', status='IN_QUEUE')"


def test_decorated_queue_reconnects_to_job(monkeypatch):
    for var in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID", "RUNPOD_DEV_SESSION"):
        monkeypatch.delenv(var, raising=False)
    app = runpod.App("demo")

    @app.queue(name="generate")
    def generate(prompt: str):
        return prompt

    job = generate.job("job-1")

    assert isinstance(job, runpod.Job)
    assert job.id == "job-1"
    assert job.done is False


def test_task_job_cannot_reconnect(monkeypatch):
    for var in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID", "RUNPOD_DEV_SESSION"):
        monkeypatch.delenv(var, raising=False)
    app = runpod.App("demo")

    @app.task(name="train")
    def train():
        return None

    with pytest.raises(InvalidResourceError, match="cannot be reconnected"):
        train.job("job-1")
