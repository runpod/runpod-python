"""
Unit tests for the in-memory JobsProgress tracker and the cross-process
PingJobMirror. Regression coverage for issue #432 (workers must not share
job state via a file on a network volume).
"""

import multiprocessing

import pytest

from runpod.serverless.modules.worker_state import (
    JobsProgress,
    PingJobMirror,
    PING_MIRROR_CAPACITY,
)


def _reset_singleton():
    JobsProgress._instance = None


class TestJobsProgressInMemory:
    def setup_method(self):
        _reset_singleton()

    def teardown_method(self):
        _reset_singleton()

    def test_singleton_identity(self):
        assert JobsProgress() is JobsProgress()

    def test_add_remove_clear_and_list(self):
        jp = JobsProgress()
        jp.add({"id": "job_1", "input": {"a": 1}})
        jp.add("job_2")

        assert jp.get_job_count() == 2
        job_list = jp.get_job_list()
        assert "job_1" in job_list and "job_2" in job_list

        jp.remove("job_1")
        assert jp.get_job_count() == 1
        assert "job_1" not in jp.get_job_list()

        jp.clear()
        assert jp.get_job_count() == 0
        assert jp.get_job_list() is None

    def test_writes_nothing_to_disk(self, tmp_path, monkeypatch):
        # Regression: JobsProgress must not persist to cwd (network-volume bug).
        monkeypatch.chdir(tmp_path)
        jp = JobsProgress()
        jp.add("job_1")
        jp.remove("job_1")
        jp.add({"id": "job_2"})
        assert list(tmp_path.iterdir()) == []

    def test_two_workers_do_not_share_jobs(self, tmp_path, monkeypatch):
        # Regression: a second worker (new singleton, same cwd) sees nothing.
        monkeypatch.chdir(tmp_path)
        worker_a = JobsProgress()
        worker_a.add("a_job")

        _reset_singleton()
        worker_b = JobsProgress()
        assert worker_b.get_job_count() == 0
        assert worker_b.get_job_list() is None


class TestPingJobMirror:
    def test_set_get_roundtrip(self):
        mirror = PingJobMirror()
        mirror.set("job_a,job_b")
        assert mirror.get() == "job_a,job_b"

    def test_empty_is_none(self):
        mirror = PingJobMirror()
        assert mirror.get() is None
        mirror.set(None)
        assert mirror.get() is None
        mirror.set("")
        assert mirror.get() is None

    def test_overflow_truncates_without_raising(self):
        mirror = PingJobMirror(capacity=32)
        # 10 ten-char ids joined by commas far exceeds 32 bytes.
        ids = ",".join(f"id{n:08d}" for n in range(10))
        mirror.set(ids)  # must not raise
        stored = mirror.get()
        assert stored is not None
        assert len(stored.encode("utf-8")) <= 32
        # Truncation happens at a comma boundary: no partial id.
        assert not stored.endswith(",")


def _child_read(mirror, queue):
    queue.put(mirror.get())


@pytest.mark.parametrize("method", ["fork", "spawn"])
def test_mirror_roundtrip_across_process(method):
    if method not in multiprocessing.get_all_start_methods():
        pytest.skip(f"start method {method} not available on this platform")
    ctx = multiprocessing.get_context(method)
    mirror = PingJobMirror(ctx=ctx)
    mirror.set("job_a,job_b")

    queue = ctx.Queue()
    proc = ctx.Process(target=_child_read, args=(mirror, queue))
    proc.start()
    result = queue.get(timeout=15)
    proc.join(timeout=15)

    assert result == "job_a,job_b"


def test_capacity_default():
    assert PING_MIRROR_CAPACITY == 65536
