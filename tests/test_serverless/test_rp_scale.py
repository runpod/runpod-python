import asyncio
from unittest.mock import AsyncMock
from dataclasses import dataclass

import pytest

from runpod.serverless.modules import rp_scale


class DummyProgress:
    def __init__(self):
        self.count = 0

    def get_job_count(self):
        return self.count

    def add(self, _):
        self.count += 1

    def remove(self, _):
        self.count = max(0, self.count - 1)


@dataclass
class PatchScaler:
    scaler: rp_scale.JobScaler
    progress: DummyProgress


def generate_job(id: str):
    return {"id": id, "input": {"test": "data"}}


@pytest.fixture
def job_scaler(monkeypatch) -> PatchScaler:
    def dummy_jobs_fetcher(input_job_id: str):
        return {"id": input_job_id, "input": {"test": "data"}}

    async def dummy_jobs_handler(_session, _config, _job):
        await asyncio.sleep(0.05)
        return None

    dummy_progress = DummyProgress()
    monkeypatch.setattr(rp_scale, "JobsProgress", lambda: dummy_progress)

    job_scaler_config = {
        "handler": lambda *_: None,
        "jobs_fetcher": dummy_jobs_fetcher,
    }
    scaler = rp_scale.JobScaler(job_scaler_config)
    scaler.jobs_handler = dummy_jobs_handler
    patch_scaler = PatchScaler(scaler=scaler, progress=dummy_progress)
    return patch_scaler


@pytest.mark.asyncio
async def test_workers_take_single_job_off_queue(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    scaler.current_concurrency = 2
    _ = asyncio.create_task(scaler.run_jobs(None))

    await scaler.jobs_queue.put(generate_job("test-1"))

    assert scaler.jobs_queue.qsize() == 1
    await asyncio.sleep(0)
    assert scaler.jobs_queue.qsize() == 0

    scaler.kill_worker()


@pytest.mark.asyncio
async def test_workers_fully_drain_queue(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    scaler.current_concurrency = 2
    _ = asyncio.create_task(scaler.run_jobs(None))

    scaler.jobs_queue = asyncio.Queue(maxsize=2)
    for i in range(2):
        await scaler.jobs_queue.put(generate_job(f"test-{i}"))

    assert scaler.jobs_queue.qsize() == 2
    await asyncio.sleep(0)
    assert scaler.jobs_queue.qsize() == 0
    scaler.kill_worker()


@pytest.mark.asyncio
async def test_workers_only_take_n_jobs(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    scaler.current_concurrency = 2
    _ = asyncio.create_task(scaler.run_jobs(None))

    scaler.jobs_queue = asyncio.Queue(maxsize=3)
    for i in range(3):
        await scaler.jobs_queue.put(generate_job(f"test-{i}"))

    assert scaler.jobs_queue.qsize() == 3
    await asyncio.sleep(0)
    assert scaler.jobs_queue.qsize() == 1

    scaler.kill_worker()

@pytest.mark.asyncio
async def test_worker_take_concurrent_jobs_dynamically(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    scaler.current_concurrency = 3
    scaler.jobs_queue = asyncio.Queue(maxsize=3)
    _ = asyncio.create_task(scaler.run_jobs(None))

    for i in range(2):
        await scaler.jobs_queue.put(generate_job(f"test-{i}"))

    assert scaler.jobs_queue.qsize() == 2
    await asyncio.sleep(0)
    assert scaler.jobs_queue.qsize() == 0

    await scaler.jobs_queue.put(generate_job(f"test-{2}"))
    assert scaler.jobs_queue.qsize() == 1
    await asyncio.sleep(0.2)
    # workers should take additional job to fill concurrency space
    assert scaler.jobs_queue.qsize() == 0

    scaler.kill_worker()


@pytest.mark.asyncio
async def test_handle_job_completes_and_clears_state(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    finished = []

    async def handler(session, config, job):
        finished.append(job["id"])

    scaler.jobs_handler = handler
    job = generate_job("handle-success")
    await scaler.jobs_queue.put(job)
    job = await scaler.jobs_queue.get()
    job_scaler.progress.add(job)

    await scaler.handle_job(AsyncMock(), job)

    assert finished == ["handle-success"]
    assert scaler.jobs_queue.qsize() == 0
    assert job_scaler.progress.count == 0

    scaler.kill_worker()

@pytest.mark.asyncio
async def test_shutdown_waits_for_inflight_job(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    job_started = asyncio.Event()
    finish_job = asyncio.Event()

    async def handler(session, config, job):
        job_started.set()
        await finish_job.wait()

    scaler.jobs_handler = handler
    scaler.current_concurrency = 1
    scaler.jobs_queue = asyncio.Queue(maxsize=1)
    run_task = asyncio.create_task(scaler.run_jobs(None))

    job = {"id": "inflight"}
    await scaler.jobs_queue.put(job)

    await asyncio.wait_for(job_started.wait(), timeout=2)

    scaler.kill_worker()
    await asyncio.sleep(0)

    assert not run_task.done()

    finish_job.set()
    await asyncio.wait_for(run_task, timeout=2)

    assert job_scaler.progress.count == 0
    assert scaler.jobs_queue.qsize() == 0

    scaler.kill_worker()


@pytest.mark.asyncio
async def test_shutdown_drains_jobs_in_queue(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    finished = []
    block = asyncio.Event()

    async def handler(session, config, job):
        await block.wait()
        finished.append(job["id"])

    scaler.jobs_handler = handler
    scaler.current_concurrency = 2
    scaler.jobs_queue = asyncio.Queue(maxsize=2)

    session = AsyncMock()

    jobs = [{"id": f"job-{idx}"} for idx in range(2)]
    for job in jobs:
        await scaler.jobs_queue.put(job)

    run_task = asyncio.create_task(scaler.run_jobs(session))
    scaler.kill_worker()

    await asyncio.sleep(0)
    assert not run_task.done()

    block.set()
    await asyncio.wait_for(run_task, timeout=2)

    assert sorted(finished) == [job["id"] for job in jobs]
    assert scaler.jobs_queue.qsize() == 0

    scaler.kill_worker()


@pytest.mark.asyncio
async def test_workers_process_jobs(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    handled = []

    async def handler(_session, _config, job):
        handled.append(job["id"])

    scaler.jobs_handler = handler
    scaler.current_concurrency = 2
    await scaler.set_scale()
    for i in range(2):
        await scaler.jobs_queue.put(generate_job(f"job-{i}"))

    asyncio.create_task(scaler.run_jobs(None))

    await asyncio.sleep(0.1)  # let workers run once

    assert handled == ["job-0", "job-1"]
    assert scaler.jobs_queue.qsize() == 0
    assert job_scaler.progress.count == 0

    scaler.kill_worker()

@pytest.mark.asyncio
async def test_get_jobs_feeds_workers_end_to_end(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    handled = []
    job_processed = asyncio.Event()

    async def handler(_session, _config, job):
        handled.append(job["id"])
        job_processed.set()

    fetch_count = {"value": 0}

    async def fetcher(_session, jobs_needed):
        if fetch_count["value"]:
            return []
        fetch_count["value"] += 1
        return [generate_job(f"job-{idx}") for idx in range(jobs_needed)]

    scaler.jobs_handler = handler
    scaler.jobs_fetcher = fetcher
    scaler.current_concurrency = 1

    session = AsyncMock()
    get_task = asyncio.create_task(scaler.get_jobs(session))

    run_jobs_task = asyncio.create_task(scaler.run_jobs(None))
    await asyncio.wait_for(job_processed.wait(), timeout=5)

    scaler.kill_worker()
    await asyncio.wait_for(get_task, timeout=5)
    await asyncio.wait_for(run_jobs_task, timeout=5)

    assert handled == ["job-0"]
    assert scaler.jobs_queue.qsize() == 0
    assert job_scaler.progress.count == 0

    scaler.kill_worker()
