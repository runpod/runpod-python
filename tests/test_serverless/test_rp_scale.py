import asyncio
import os
import signal
import threading
import time
from unittest.mock import AsyncMock, Mock, patch
from dataclasses import dataclass

import pytest

from runpod.http_client import TooManyRequests
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
async def test_stop_job_cancels_inflight_task(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    job_started = asyncio.Event()
    cancelled = []

    async def handler(_session, _config, job):
        job_started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(job["id"])
            raise

    scaler.jobs_handler = handler
    scaler.current_concurrency = 1
    scaler.jobs_queue = asyncio.Queue(maxsize=1)
    run_task = asyncio.create_task(scaler.run_jobs(None))

    await scaler.jobs_queue.put(generate_job("stop-me"))
    await asyncio.wait_for(job_started.wait(), timeout=2)

    assert "stop-me" in scaler.jobs_tasks
    assert await scaler.stop_job("stop-me") is True

    scaler.kill_worker()
    await asyncio.wait_for(run_task, timeout=2)

    assert cancelled == ["stop-me"]
    assert "stop-me" not in scaler.jobs_tasks
    assert job_scaler.progress.count == 0

    scaler.kill_worker()


@pytest.mark.asyncio
async def test_stop_job_unknown_id_returns_false(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    assert await scaler.stop_job("does-not-exist") is False
    scaler.kill_worker()


@pytest.mark.asyncio
async def test_monitor_stop_signals_stops_jobs(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    stopped = []

    async def fake_stop_job(job_id):
        stopped.append(job_id)
        return True

    async def fetcher(_session):
        if not stopped:
            return ["job-a", "job-b"]
        return []

    scaler.stop_job = fake_stop_job
    scaler.stop_signals_fetcher = fetcher

    monitor_task = asyncio.create_task(scaler.monitor_stop_signals(AsyncMock()))
    await asyncio.sleep(0.05)
    scaler.kill_worker()
    await asyncio.wait_for(monitor_task, timeout=2)

    assert sorted(stopped) == ["job-a", "job-b"]


@pytest.mark.asyncio
async def test_monitor_stop_signals_survives_errors(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    calls = {"value": 0}

    async def fetcher(_session):
        calls["value"] += 1
        raise RuntimeError("boom")

    scaler.stop_signals_fetcher = fetcher

    monitor_task = asyncio.create_task(scaler.monitor_stop_signals(AsyncMock()))
    await asyncio.sleep(0.05)
    scaler.kill_worker()
    await asyncio.wait_for(monitor_task, timeout=2)

    assert calls["value"] >= 1


@pytest.mark.asyncio
async def test_monitor_stop_signals_real_stop_job_cancels_task(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    started = asyncio.Event()
    cancelled = []

    async def long_task():
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    task = asyncio.create_task(long_task())
    await asyncio.wait_for(started.wait(), timeout=2)
    scaler.jobs_tasks["job-x"] = task

    sent = {"value": False}

    async def fetcher(_session):
        if not sent["value"]:
            sent["value"] = True
            return ["job-x"]
        return []

    scaler.stop_signals_fetcher = fetcher

    monitor_task = asyncio.create_task(scaler.monitor_stop_signals(AsyncMock()))
    await asyncio.sleep(0.05)
    scaler.kill_worker()
    await asyncio.wait_for(monitor_task, timeout=2)

    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled()
    assert cancelled == [True]


@pytest.mark.asyncio
async def test_monitor_stop_signals_debounces_too_many_requests(
    job_scaler: PatchScaler, monkeypatch
):
    scaler = job_scaler.scaler
    sleeps = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        sleeps.append(delay)
        await real_sleep(0)

    async def fetcher(_session):
        raise TooManyRequests(Mock(), (), status=429, message="rate limited")

    scaler.stop_signals_fetcher = fetcher
    monkeypatch.setattr(rp_scale.asyncio, "sleep", fake_sleep)

    monitor_task = asyncio.create_task(scaler.monitor_stop_signals(AsyncMock()))
    await real_sleep(0.05)
    scaler.kill_worker()
    await asyncio.wait_for(monitor_task, timeout=2)

    assert 5 in sleeps


@pytest.mark.asyncio
async def test_monitor_stop_signals_reraises_cancelled(job_scaler: PatchScaler):
    scaler = job_scaler.scaler

    async def fetcher(_session):
        raise asyncio.CancelledError()

    scaler.stop_signals_fetcher = fetcher

    monitor_task = asyncio.create_task(scaler.monitor_stop_signals(AsyncMock()))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(monitor_task, timeout=2)


@pytest.mark.asyncio
async def test_monitor_stop_signals_disabled_without_stop_url(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    scaler.stop_signals_fetcher = rp_scale.get_stop_signals

    with patch.object(rp_scale, "_job_stop_url", return_value=None):
        await asyncio.wait_for(scaler.monitor_stop_signals(AsyncMock()), timeout=1)


# Well under the old 100ms dispatch poll, so a polling loop fails these tests.
DISPATCH_DEADLINE = 0.05  # seconds


def _gated_fetcher(release: asyncio.Event, job_ids: list[str]):
    """Fetcher that hands out the given jobs once `release` is set, then none."""
    pending = list(job_ids)

    async def fetcher(_session, jobs_needed):
        await release.wait()
        taken, pending[:] = pending[:jobs_needed], pending[jobs_needed:]
        if not taken:
            await asyncio.sleep(1)
        return [generate_job(job_id) for job_id in taken]

    return fetcher


@pytest.mark.asyncio
async def test_idle_dispatcher_starts_fetched_job_immediately(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    release = asyncio.Event()
    handler_called = asyncio.Event()

    async def handler(_session, _config, _job):
        handler_called.set()

    scaler.jobs_handler = handler
    scaler.jobs_fetcher = _gated_fetcher(release, ["job-0"])
    get_task = asyncio.create_task(scaler.get_jobs(AsyncMock()))
    run_task = asyncio.create_task(scaler.run_jobs(None))
    await asyncio.sleep(0.01)  # dispatcher is now idle

    release.set()

    await asyncio.wait_for(handler_called.wait(), timeout=DISPATCH_DEADLINE)
    scaler.kill_worker()
    get_task.cancel()
    await asyncio.wait_for(run_task, timeout=1)


@pytest.mark.asyncio
async def test_busy_dispatcher_starts_second_job_immediately(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    release_second = asyncio.Event()
    finish_first = asyncio.Event()
    started = {}

    async def handler(_session, _config, job):
        started[job["id"]] = True
        if job["id"] == "job-0":
            await finish_first.wait()

    scaler.jobs_handler = handler
    scaler.current_concurrency = 2
    scaler.jobs_queue = asyncio.Queue(maxsize=2)
    await scaler.jobs_queue.put(generate_job("job-0"))
    job_scaler.progress.add(None)
    run_task = asyncio.create_task(scaler.run_jobs(None))
    await asyncio.sleep(0.01)  # job-0 running, dispatcher waiting on it
    assert started == {"job-0": True}

    scaler.jobs_fetcher = _gated_fetcher(release_second, ["job-1"])
    get_task = asyncio.create_task(scaler.get_jobs(AsyncMock()))
    await asyncio.sleep(0)
    release_second.set()

    async def second_started():
        while "job-1" not in started:
            await asyncio.sleep(0)

    await asyncio.wait_for(second_started(), timeout=DISPATCH_DEADLINE)
    finish_first.set()
    scaler.kill_worker()
    get_task.cancel()
    await asyncio.wait_for(run_task, timeout=1)


@pytest.mark.asyncio
async def test_idle_dispatcher_exits_promptly_on_shutdown(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    run_task = asyncio.create_task(scaler.run_jobs(None))
    await asyncio.sleep(0.01)  # dispatcher is now idle

    scaler.kill_worker()

    await asyncio.wait_for(run_task, timeout=DISPATCH_DEADLINE)


@pytest.mark.asyncio
async def test_idle_dispatcher_exits_when_shutdown_follows_loop_check(
    job_scaler: PatchScaler,
):
    scaler = job_scaler.scaler
    real_is_alive = scaler.is_alive
    checks = 0

    def is_alive_then_signalled():
        # A signal handler can run between run_jobs' loop check and its wait.
        nonlocal checks
        checks += 1
        alive = real_is_alive()
        if checks == 1:
            scaler.kill_worker()
        return alive

    scaler.is_alive = is_alive_then_signalled

    await asyncio.wait_for(scaler.run_jobs(None), timeout=DISPATCH_DEADLINE)


SIGNAL_DELAY = 0.1  # seconds
SIGNAL_WAKE_TIMEOUT = 2  # seconds; the only timer that would wake a stalled loop


@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="needs POSIX signals")
def test_idle_dispatcher_exits_promptly_on_os_signal(job_scaler: PatchScaler):
    scaler = job_scaler.scaler
    signalled_at = []

    def handler(signum, frame):
        signalled_at.append(time.monotonic())
        scaler.handle_shutdown(signum, frame)

    async def run_until_signalled():
        run_task = asyncio.create_task(scaler.run_jobs(None))
        # Deliver the signal once the loop is blocked in the selector.
        threading.Timer(
            SIGNAL_DELAY, os.kill, (os.getpid(), signal.SIGUSR1)
        ).start()
        await asyncio.wait_for(run_task, timeout=SIGNAL_WAKE_TIMEOUT)
        return time.monotonic()

    previous = signal.signal(signal.SIGUSR1, handler)
    try:
        finished_at = asyncio.run(run_until_signalled())
    finally:
        signal.signal(signal.SIGUSR1, previous)

    assert finished_at - signalled_at[0] < DISPATCH_DEADLINE


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


def _full_worker(job_scaler: PatchScaler, concurrency: int, release: dict[str, asyncio.Event]):
    """Start get_jobs/run_jobs with `concurrency` jobs that finish when released."""
    scaler = job_scaler.scaler
    scaler.current_concurrency = concurrency
    fetches = []
    refetched = asyncio.Event()

    async def fetcher(_session, jobs_needed):
        fetches.append(jobs_needed)
        if len(fetches) == 1:
            return [generate_job(job_id) for job_id in release]
        refetched.set()
        await asyncio.sleep(10)  # long-poll with nothing queued
        return []

    async def handler(_session, _config, job):
        await release[job["id"]].wait()

    scaler.jobs_fetcher = fetcher
    scaler.jobs_handler = handler
    tasks = (asyncio.create_task(scaler.get_jobs(AsyncMock())),
             asyncio.create_task(scaler.run_jobs(None)))
    return fetches, refetched, tasks


async def _stop(job_scaler: PatchScaler, release, tasks):
    job_scaler.scaler.kill_worker()
    for event in release.values():
        event.set()
    tasks[0].cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_full_worker_takes_a_full_batch_as_soon_as_it_drains(job_scaler: PatchScaler):
    """A job-take costs ~0.5 s however many jobs it asks for, so a full worker
    takes again once every slot is free rather than sleeping a fixed second."""
    release = {f"job-{i}": asyncio.Event() for i in range(4)}
    fetches, refetched, tasks = _full_worker(job_scaler, 4, release)
    await asyncio.sleep(0.05)  # all four running; the worker is full

    for event in release.values():
        event.set()

    await asyncio.wait_for(refetched.wait(), timeout=DISPATCH_DEADLINE)
    assert fetches == [4, 4]
    await _stop(job_scaler, release, tasks)


@pytest.mark.asyncio
async def test_full_worker_does_not_take_while_partly_drained(
    job_scaler: PatchScaler, monkeypatch
):
    monkeypatch.setattr(rp_scale, "FULL_WAIT_SECONDS", 10)
    release = {f"job-{i}": asyncio.Event() for i in range(4)}
    fetches, refetched, tasks = _full_worker(job_scaler, 4, release)
    await asyncio.sleep(0.05)

    release["job-0"].set()
    await asyncio.sleep(0.1)

    assert fetches == [4]
    await _stop(job_scaler, release, tasks)


@pytest.mark.asyncio
async def test_full_worker_takes_freed_slots_after_the_wait_bound(
    job_scaler: PatchScaler, monkeypatch
):
    """A straggler can't hold the other slots idle longer than the old sleep."""
    monkeypatch.setattr(rp_scale, "FULL_WAIT_SECONDS", 0.1)
    release = {f"job-{i}": asyncio.Event() for i in range(4)}
    fetches, refetched, tasks = _full_worker(job_scaler, 4, release)
    await asyncio.sleep(0.05)

    for job_id in ("job-0", "job-1", "job-2"):
        release[job_id].set()

    await asyncio.wait_for(refetched.wait(), timeout=1)
    assert fetches == [4, 3]
    await _stop(job_scaler, release, tasks)


@pytest.mark.asyncio
async def test_full_worker_stops_taking_promptly_on_shutdown(job_scaler: PatchScaler):
    release = {"job-0": asyncio.Event()}
    fetches, _refetched, tasks = _full_worker(job_scaler, 1, release)
    await asyncio.sleep(0.05)  # full: get_jobs is waiting to drain

    job_scaler.scaler.kill_worker()

    await asyncio.wait_for(tasks[0], timeout=DISPATCH_DEADLINE)
    assert fetches == [1]
    release["job-0"].set()
    await asyncio.wait_for(tasks[1], timeout=1)


@pytest.mark.asyncio
async def test_zero_concurrency_waits_instead_of_spinning(job_scaler: PatchScaler):
    """A modifier returning 0 on an empty worker must still yield to the loop."""
    scaler = job_scaler.scaler
    calls = []

    def zero_concurrency(_current):
        calls.append(1)
        if len(calls) >= 50:
            scaler.kill_worker()  # bound the spin so the failure is an assert, not a hang
        return 0

    scaler.concurrency_modifier = zero_concurrency
    task = asyncio.create_task(scaler.get_jobs(AsyncMock()))
    await asyncio.sleep(0.05)

    assert len(calls) == 1
    scaler.kill_worker()
    await asyncio.wait_for(task, timeout=DISPATCH_DEADLINE)
