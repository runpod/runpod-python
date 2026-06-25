"""JobScaler occupancy and run_worker mirror wiring."""

import pytest

from runpod.serverless.modules.rp_scale import JobScaler
from runpod.serverless.modules.worker_state import JobsProgress


@pytest.fixture(autouse=True)
def _reset_singleton():
    JobsProgress._instance = None
    yield
    JobsProgress._instance = None


def test_occupancy_counts_single_in_flight_job():
    # Concurrency 1 + one in-progress job => occupancy 1, so jobs_needed == 0.
    scaler = JobScaler({"handler": lambda x: x})
    scaler.job_progress.add("job_1")
    assert scaler.current_occupancy() == 1
    assert scaler.current_concurrency - scaler.current_occupancy() == 0


def test_run_worker_attaches_one_shared_mirror():
    """run_worker attaches one mirror to JobsProgress and passes the same
    instance to the ping process."""
    import runpod.serverless.worker as worker_mod
    from unittest.mock import MagicMock, patch

    captured = {}

    def fake_job_scaler(config):
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

    # The ping got a mirror, and it is the same instance the job tracker writes.
    assert captured["ping_mirror"] is not None
    assert JobsProgress()._mirror is captured["ping_mirror"]
