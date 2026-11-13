"""
A finished job frees its slot before its result is sent, so the next job-take
overlaps the result upload instead of waiting for it.

The upload is a full round trip to ai-api (~0.3 s measured on serverless), and
a single-slot worker used to wait it out before asking for the next job: every
job queued behind another paid it again. The refresh guarantee (SLS-900) still
holds: a job asking for a refresh stops intake before its slot is freed.
"""

import asyncio
import time

import pytest

from runpod.serverless.modules import rp_http, rp_job, rp_scale

from .test_refresh_worker import DEADLINE, FakeAiApi, _sync_handler, generate_job

UPLOAD_S = 0.3


class SlowUploadAiApi(FakeAiApi):
    """ai-api whose job-done answers only after UPLOAD_S, recording when each
    job-take arrived and when each upload finished."""

    def __init__(self, jobs):
        super().__init__(jobs)
        self.take_at: dict[str, float] = {}
        self.done_at: dict[str, float] = {}

    async def job_take(self, request):
        response = await super().job_take(request)
        if self.takes and self.takes[-1] is not None:
            self.take_at.setdefault(self.takes[-1], time.monotonic())
        return response

    async def job_done(self, request):
        await asyncio.sleep(UPLOAD_S)
        response = await super().job_done(request)
        self.done_at[request.match_info["job_id"]] = time.monotonic()
        return response


@pytest.fixture
async def slow_upload_worker(monkeypatch):
    servers = []

    async def make(jobs):
        server = SlowUploadAiApi(jobs)
        await server.start()
        servers.append(server)
        monkeypatch.setattr(rp_job, "JOB_GET_URL", server.job_take_url.replace("$ID", "pod-1"))
        monkeypatch.setattr(
            rp_http, "JOB_DONE_URL", server.job_done_url.replace("$RUNPOD_POD_ID", "pod-1")
        )
        return server

    yield make
    for server in servers:
        await server.close()


async def _serve_until_drained(server, scaler):
    run = asyncio.create_task(scaler.run())
    try:
        await asyncio.wait_for(server.polled_empty.wait(), timeout=DEADLINE)
        # the last upload may still be in flight when the queue reads empty
        deadline = time.monotonic() + DEADLINE
        while len(server.done) < len(server.take_at) and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
    finally:
        scaler.kill_worker()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


@pytest.mark.asyncio
async def test_next_job_is_taken_while_the_previous_result_uploads(slow_upload_worker):
    server = await slow_upload_worker([generate_job(j, n=i) for i, j in enumerate("ABC")])
    await _serve_until_drained(server, rp_scale.JobScaler({"handler": _sync_handler}))

    assert server.takes[:3] == ["A", "B", "C"]
    assert server.done == {j: {"output": {"echo": i}} for i, j in enumerate("ABC")}
    assert server.take_at["B"] < server.done_at["A"]
    assert server.take_at["C"] < server.done_at["B"]


@pytest.mark.asyncio
async def test_uploads_no_longer_add_up_across_queued_jobs(slow_upload_worker):
    jobs = [generate_job(f"J{i}", n=i) for i in range(5)]
    server = await slow_upload_worker(jobs)
    await _serve_until_drained(server, rp_scale.JobScaler({"handler": _sync_handler}))

    elapsed = server.done_at["J4"] - server.take_at["J0"]
    # serial: 5 uploads back to back (1.5 s); overlapped: about one upload
    # plus the takes, which this fake answers at once
    assert elapsed < 3 * UPLOAD_S, f"uploads were serialized: {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_refresh_still_leaves_the_next_job_queued(slow_upload_worker):
    server = await slow_upload_worker(
        [generate_job("A", n=1, refresh=True), generate_job("B", n=2)]
    )
    scaler = rp_scale.JobScaler({"handler": _sync_handler})
    run = asyncio.create_task(scaler.run())
    try:
        _, pending = await asyncio.wait({run}, timeout=DEADLINE)
        assert server.takes == ["A"], "job B was taken after stopPod and would be lost"
        assert [job["id"] for job in server.queue] == ["B"]
        assert server.done == {"A": {"output": {"echo": 1}, "stopPod": True}}
        assert not pending
    finally:
        scaler.kill_worker()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


@pytest.mark.asyncio
async def test_custom_jobs_handler_keeps_the_slot_until_it_returns():
    """Local-test jobs handlers have no on_result_ready; they keep the old contract."""
    seen = []

    async def handler(session, config, job):
        seen.append(job["id"])
        return {"output": job["id"]}

    scaler = rp_scale.JobScaler({"handler": _sync_handler})
    scaler.jobs_handler = handler
    job = generate_job("A")
    scaler.job_progress.add(job)
    await scaler.jobs_queue.put(job)
    await scaler.jobs_queue.get()
    await scaler.handle_job(None, job)
    assert seen == ["A"]
    assert scaler.current_occupancy() == 0
    assert scaler._sending_results == set()
