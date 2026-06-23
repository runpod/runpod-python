"""JobScaler keeps the PingJobMirror in sync and reports correct occupancy."""

import pytest

from runpod.serverless.modules.rp_scale import JobScaler
from runpod.serverless.modules.worker_state import JobsProgress, PingJobMirror


@pytest.fixture(autouse=True)
def _reset_singleton():
    JobsProgress._instance = None
    yield
    JobsProgress._instance = None


def _make_scaler(mirror):
    return JobScaler({"handler": lambda x: x}, job_mirror=mirror)


def test_mirror_syncs_on_add():
    mirror = PingJobMirror()
    scaler = _make_scaler(mirror)

    scaler.job_progress.add("job_1")
    scaler._sync_mirror()

    assert "job_1" in mirror.get()


def test_mirror_syncs_on_remove():
    mirror = PingJobMirror()
    scaler = _make_scaler(mirror)

    scaler.job_progress.add("job_1")
    scaler._sync_mirror()
    scaler.job_progress.remove("job_1")
    scaler._sync_mirror()

    assert mirror.get() is None


def test_sync_mirror_is_noop_without_mirror():
    scaler = _make_scaler(None)
    scaler.job_progress.add("job_1")
    scaler._sync_mirror()  # must not raise


def test_occupancy_counts_single_in_flight_job():
    # Concurrency 1 + one in-progress job => occupancy 1, so jobs_needed == 0.
    scaler = _make_scaler(None)
    scaler.job_progress.add("job_1")
    assert scaler.current_occupancy() == 1
    assert scaler.current_concurrency - scaler.current_occupancy() == 0
