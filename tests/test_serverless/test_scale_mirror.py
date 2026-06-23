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


def test_run_worker_shares_one_mirror():
    """run_worker creates one mirror and passes it to both ping and scaler."""
    import runpod.serverless.worker as worker_mod
    from unittest.mock import MagicMock, patch

    captured = {}

    def fake_job_scaler(config, job_mirror=None):
        captured["scaler_mirror"] = job_mirror
        scaler = MagicMock()
        scaler.start = MagicMock()
        return scaler

    def fake_start_ping(mirror=None, test=False):
        captured["ping_mirror"] = mirror

    with patch.object(worker_mod, "run_fitness_checks", return_value=None), \
         patch("asyncio.run", lambda *a, **k: None), \
         patch.object(worker_mod.heartbeat, "start_ping", side_effect=fake_start_ping), \
         patch.object(worker_mod.rp_scale, "JobScaler", side_effect=fake_job_scaler):
        worker_mod.run_worker({"handler": lambda x: x})

    assert captured["ping_mirror"] is not None
    assert captured["ping_mirror"] is captured["scaler_mirror"]
