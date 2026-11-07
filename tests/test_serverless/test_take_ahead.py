"""Tests for opt-in job take-ahead in JobScaler (#647)."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from runpod.serverless.modules import rp_scale

DEADLINE = 2


class Progress:
    def __init__(self):
        self.ids = set()

    def add(self, job):
        self.ids.add(job["id"])

    def remove(self, job):
        self.ids.discard(job["id"])

    def get_job_count(self):
        return len(self.ids)


@pytest.fixture(autouse=True)
def progress(monkeypatch) -> Progress:
    dummy = Progress()
    monkeypatch.setattr(rp_scale, "JobsProgress", lambda: dummy)
    monkeypatch.delenv(rp_scale.TAKE_AHEAD_ENV, raising=False)
    return dummy


def job(job_id: str) -> dict:
    return {"id": job_id, "input": {}}


class Platform:
    """A job queue that answers takes from a script of batches.

    Each take pops the next batch and returns at most the number of jobs the
    worker asked for. Once the script runs out, takes block like a long-poll
    on an empty queue until the test ends.
    """

    def __init__(self, batches: list[list[str]]):
        self.batches = list(batches)
        self.asked: list[int] = []
        self.took = asyncio.Event()

    async def take(self, _session, count: int):
        self.asked.append(count)
        self.took.set()
        if not self.batches:
            await asyncio.sleep(60)
            return []
        return [job(job_id) for job_id in self.batches.pop(0)[:count]]

    async def wait_for_takes(self, count: int):
        while len(self.asked) < count:
            self.took.clear()
            await asyncio.wait_for(self.took.wait(), timeout=DEADLINE)


class Worker:
    def __init__(self, platform: Platform, concurrency: int, take_ahead: int):
        self.release: dict[str, asyncio.Event] = {}
        self.started: list[str] = []
        self.finished: list[str] = []
        self.stopping = False
        self.scaler = rp_scale.JobScaler(
            {"handler": lambda _: None, "take_ahead": take_ahead}
        )
        self.scaler.jobs_fetcher = platform.take
        self.scaler.jobs_handler = self._handle
        self.scaler.current_concurrency = concurrency
        self.tasks = (
            asyncio.create_task(self.scaler.get_jobs(AsyncMock())),
            asyncio.create_task(self.scaler.run_jobs(None)),
        )

    async def _handle(self, _session, _config, job_):
        self.started.append(job_["id"])
        if not self.stopping:
            await self.release.setdefault(job_["id"], asyncio.Event()).wait()
        self.finished.append(job_["id"])

    def finish(self, *job_ids: str):
        for job_id in job_ids:
            self.release.setdefault(job_id, asyncio.Event()).set()

    async def stop(self):
        # Shutdown drains held jobs, so let any job that starts now finish.
        self.stopping = True
        self.scaler.kill_worker()
        for event in self.release.values():
            event.set()
        self.tasks[0].cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


async def settle():
    """Let every ready task run until the workers are idle again."""
    # Plain yields first: the handoffs under test are pure event-loop steps,
    # so this does not depend on how loaded the machine is.
    for _ in range(100):
        await asyncio.sleep(0)
    await asyncio.sleep(0.05)


# --- configuration ---


def test_take_ahead_is_off_by_default():
    assert rp_scale._take_ahead_from({}) == 0
    assert rp_scale.JobScaler({"handler": lambda _: None}).take_ahead == 0


def test_take_ahead_reads_env(monkeypatch):
    monkeypatch.setenv(rp_scale.TAKE_AHEAD_ENV, "16")
    assert rp_scale._take_ahead_from({}) == 16


def test_take_ahead_config_overrides_env(monkeypatch):
    monkeypatch.setenv(rp_scale.TAKE_AHEAD_ENV, "16")
    assert rp_scale._take_ahead_from({"take_ahead": 4}) == 4
    assert rp_scale._take_ahead_from({"take_ahead": 0}) == 0


@pytest.mark.parametrize("value", ["lots", "1.5", -3, "-1", True, [8]])
def test_invalid_take_ahead_is_ignored(value):
    assert rp_scale._take_ahead_from({"take_ahead": value}) == 0


def test_take_ahead_is_disabled_with_refresh_worker():
    """A refresh worker exits after one job, so it must not hold others."""
    config = {"take_ahead": 8, "refresh_worker": True}
    assert rp_scale._take_ahead_from(config) == 0


@pytest.mark.asyncio
async def test_queue_holds_concurrency_plus_take_ahead():
    scaler = rp_scale.JobScaler({"handler": lambda _: None, "take_ahead": 3})
    scaler.current_concurrency = 4
    await scaler.set_scale()
    assert scaler.jobs_queue.maxsize == 7


# --- scheduling ---


@pytest.mark.asyncio
async def test_without_take_ahead_a_full_worker_waits_to_drain():
    """Baseline (main): the next take waits for running jobs to finish."""
    platform = Platform([["a", "b"], ["c", "d"]])
    worker = Worker(platform, concurrency=2, take_ahead=0)
    await settle()

    assert platform.asked == [2]
    assert worker.started == ["a", "b"]
    await worker.stop()


@pytest.mark.asyncio
async def test_deep_queue_takes_ahead_while_slots_are_busy():
    """After a filled take, a full worker asks for take_ahead jobs right away,
    so the take overlaps the running jobs instead of following them."""
    platform = Platform([["a", "b"], ["c", "d"]])
    worker = Worker(platform, concurrency=2, take_ahead=2)
    await platform.wait_for_takes(2)
    await settle()

    assert platform.asked[:2] == [2, 2]
    # Held jobs wait for a slot; concurrency still caps what runs.
    assert worker.started == ["a", "b"]
    assert worker.scaler.jobs_queue.qsize() == 2

    worker.finish("a")
    await settle()
    assert worker.started == ["a", "b", "c"]
    await worker.stop()


@pytest.mark.asyncio
async def test_holding_worker_takes_again_only_once_its_buffer_drains():
    platform = Platform([["a", "b"], ["c", "d"], ["e", "f"]])
    worker = Worker(platform, concurrency=2, take_ahead=2)
    await platform.wait_for_takes(2)
    await settle()

    worker.finish("a")  # "c" leaves the buffer, "d" is still held
    await settle()
    assert len(platform.asked) == 2

    worker.finish("b")  # "d" starts: the buffer is empty, take again
    await platform.wait_for_takes(3)
    assert platform.asked[2] == 2  # every slot busy: ask only for take_ahead
    await worker.stop()


@pytest.mark.asyncio
async def test_a_short_take_asks_only_for_free_slots():
    platform = Platform([["a", "b", "c"]])
    worker = Worker(platform, concurrency=4, take_ahead=2)
    await platform.wait_for_takes(1)
    # 4 asked, 3 returned: the queue is shallow, so no take-ahead yet.
    await platform.wait_for_takes(2)
    assert platform.asked[:2] == [4, 1]
    await worker.stop()


@pytest.mark.asyncio
async def test_shallow_queue_does_not_take_ahead():
    """A take that comes back short means the queue is shallow; holding jobs
    then would only keep them from idle workers."""
    platform = Platform([["a"], []])
    worker = Worker(platform, concurrency=1, take_ahead=4)
    await settle()

    # The first take asked for 1 and got 1, so the second asks for 4 more.
    # It returns nothing, and the third take must not ask for extra jobs.
    await platform.wait_for_takes(2)
    assert platform.asked[:2] == [1, 4]
    worker.finish("a")
    await platform.wait_for_takes(3)
    assert platform.asked[2] == 1
    await worker.stop()


@pytest.mark.asyncio
async def test_zero_concurrency_never_takes_ahead(monkeypatch):
    monkeypatch.setattr(rp_scale, "FULL_WAIT_SECONDS", 0.01)
    platform = Platform([["a"]])
    scaler = rp_scale.JobScaler({"handler": lambda _: None, "take_ahead": 4})
    scaler.jobs_fetcher = platform.take
    scaler._queue_is_deep = True
    scaler.concurrency_modifier = lambda _current: 0
    task = asyncio.create_task(scaler.get_jobs(AsyncMock()))
    await asyncio.sleep(0.1)

    assert platform.asked == []
    scaler.kill_worker()
    await asyncio.wait_for(task, timeout=DEADLINE)


# --- stopping and shutdown ---


@pytest.mark.asyncio
async def test_stop_signal_drops_a_held_job_before_it_starts(progress):
    platform = Platform([["a"], ["b"]])
    worker = Worker(platform, concurrency=1, take_ahead=1)
    await platform.wait_for_takes(2)
    await settle()
    assert worker.scaler.jobs_queue.qsize() == 1

    assert await worker.scaler.stop_job("b") is True
    worker.finish("a")
    await settle()

    assert worker.started == ["a"]
    assert progress.ids == set()
    assert worker.scaler.jobs_queue.qsize() == 0
    await worker.stop()


@pytest.mark.asyncio
async def test_stop_signal_for_unknown_job_still_returns_false():
    worker = Worker(Platform([]), concurrency=1, take_ahead=1)
    await settle()
    assert await worker.scaler.stop_job("nope") is False
    await worker.stop()


@pytest.mark.asyncio
async def test_shutdown_still_runs_held_jobs(progress):
    """Held jobs were taken from the platform, so shutdown drains them like
    any other queued job instead of dropping them."""
    platform = Platform([["a"], ["b"]])
    worker = Worker(platform, concurrency=1, take_ahead=1)
    await platform.wait_for_takes(2)
    await settle()

    worker.scaler.kill_worker()
    worker.finish("a", "b")
    await asyncio.wait_for(worker.tasks[1], timeout=DEADLINE)

    assert worker.finished == ["a", "b"]
    assert progress.ids == set()
    worker.tasks[0].cancel()
    await asyncio.gather(*worker.tasks, return_exceptions=True)
