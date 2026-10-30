"""
Per-job refresh_worker must stop job intake before the slot frees (SLS-900).

A handler returning {"refresh_worker": True} makes the SDK send stopPod with the
result. The platform then restarts the worker, killing whatever it runs at that
moment. If the worker takes another job between sending stopPod and the restart,
that job is lost. The start-time refresh_worker flag never had this race; the
per-job return value did.

These tests cover the scaler with a fake queue, rp_job.handle_job's return
value, the real JobScaler talking HTTP to a fake ai-api, and a real worker
process started through runpod.serverless.start.
"""

import asyncio
import json
import os
import sys
import textwrap
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web

from runpod.serverless.modules import rp_http, rp_job, rp_scale

DEADLINE = 5


def generate_job(job_id: str, **job_input: Any) -> dict[str, Any]:
    return {"id": job_id, "input": job_input}


# ---------------------------------------------------------------------------
# JobScaler with a fake queue
# ---------------------------------------------------------------------------


class DummyProgress:
    def __init__(self):
        self.count = 0
        self.on_remove = None

    def get_job_count(self):
        return self.count

    def add(self, _):
        self.count += 1

    def remove(self, job):
        if self.on_remove:
            self.on_remove(job)
        self.count = max(0, self.count - 1)


class FakeQueue:
    """Stands in for the platform: a job queue and a record of every take."""

    def __init__(self, job_ids):
        self.queue = [generate_job(job_id) for job_id in job_ids]
        self.taken: list[str] = []
        self.refresh_sent = False
        self.takes_after_refresh = 0
        self.polled_empty = asyncio.Event()

    async def fetch(self, _session, jobs_needed):
        if self.refresh_sent:
            self.takes_after_refresh += 1
        if not self.queue:
            self.polled_empty.set()
            await asyncio.sleep(0.01)  # long-poll with nothing queued
            return []
        jobs, self.queue = self.queue[:jobs_needed], self.queue[jobs_needed:]
        self.taken.extend(job["id"] for job in jobs)
        return jobs


@pytest.fixture
def progress(monkeypatch) -> DummyProgress:
    dummy = DummyProgress()
    monkeypatch.setattr(rp_scale, "JobsProgress", lambda: dummy)
    return dummy


def start_worker(queue: FakeQueue, results: dict[str, Any], config=None, concurrency=1):
    """Run get_jobs/run_jobs; each job's handler returns results[job_id]
    (default: a plain output), the way rp_job.handle_job returns what it sent."""
    scaler = rp_scale.JobScaler({"handler": lambda _: None, **(config or {})})
    scaler.current_concurrency = concurrency
    release: dict[str, asyncio.Event] = {}

    async def handler(_session, _config, job):
        if job["id"] in release:
            await release[job["id"]].wait()
        result = results.get(job["id"], {"output": "ok"})
        if isinstance(result, dict) and result.get("stopPod"):
            queue.refresh_sent = True
        return result

    scaler.jobs_fetcher = queue.fetch
    scaler.jobs_handler = handler
    tasks = (
        asyncio.create_task(scaler.get_jobs(AsyncMock())),
        asyncio.create_task(scaler.run_jobs(None)),
    )
    return scaler, tasks, release


async def wait_for_exit(tasks, timeout=DEADLINE) -> bool:
    """Whether both loops end on their own within the timeout."""
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    return not pending


async def stop(scaler, tasks, release=None):
    scaler.kill_worker()
    for event in (release or {}).values():
        event.set()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


REFRESH_RESULTS = [
    pytest.param({"stopPod": True}, id="stop-only"),
    pytest.param({"output": {"mesh": "done"}, "stopPod": True}, id="with-output"),
    pytest.param({"error": "handler failed", "stopPod": True}, id="with-error"),
]

NON_REFRESH_RESULTS = [
    pytest.param(None, id="none"),
    pytest.param({}, id="empty-dict"),
    pytest.param({"output": "ok"}, id="output"),
    pytest.param({"output": "ok", "stopPod": False}, id="stop-false"),
    pytest.param({"output": {"stopPod": True}}, id="stop-nested-in-output"),
    pytest.param({"error": "handler failed"}, id="error"),
    pytest.param("stopPod", id="string"),
    pytest.param(["stopPod"], id="list"),
    pytest.param(True, id="bool"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh_result", REFRESH_RESULTS)
async def test_per_job_refresh_takes_no_further_job(progress, refresh_result):
    """The SLS-900 race: job B is queued when job A asks for a refresh."""
    queue = FakeQueue(["A", "B"])
    scaler, tasks, _ = start_worker(queue, {"A": refresh_result})
    try:
        exited = await wait_for_exit(tasks)
        assert queue.taken == ["A"], "the worker took a job after requesting a refresh"
        assert queue.takes_after_refresh == 0
        assert [job["id"] for job in queue.queue] == ["B"]
        assert exited, "the worker kept running after requesting a refresh"
        assert not scaler.is_alive()
        assert progress.count == 0
    finally:
        await stop(scaler, tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize("result", NON_REFRESH_RESULTS)
async def test_job_without_refresh_keeps_taking_jobs(progress, result):
    queue = FakeQueue(["A", "B"])
    scaler, tasks, _ = start_worker(queue, {"A": result, "B": result})
    try:
        await asyncio.wait_for(queue.polled_empty.wait(), timeout=DEADLINE)
        assert queue.taken == ["A", "B"]
        assert scaler.is_alive()
        assert not any(task.done() for task in tasks)
    finally:
        await stop(scaler, tasks)


@pytest.mark.asyncio
async def test_refresh_on_a_later_job_stops_after_that_job(progress):
    queue = FakeQueue(["A", "B", "C", "D"])
    scaler, tasks, _ = start_worker(queue, {"C": {"output": "ok", "stopPod": True}})
    try:
        assert await wait_for_exit(tasks)
        assert queue.taken == ["A", "B", "C"]
        assert [job["id"] for job in queue.queue] == ["D"]
    finally:
        await stop(scaler, tasks)


@pytest.mark.asyncio
async def test_intake_stops_before_the_slot_frees(progress):
    """get_jobs must see the worker as dead the moment the slot frees, or it
    takes the next job before the platform restarts the worker."""
    alive_when_freed = []
    queue = FakeQueue(["A", "B"])
    scaler, tasks, _ = start_worker(queue, {"A": {"stopPod": True}})
    progress.on_remove = lambda _job: alive_when_freed.append(scaler.is_alive())
    try:
        assert await wait_for_exit(tasks)
        assert alive_when_freed == [False]
    finally:
        await stop(scaler, tasks)


@pytest.mark.asyncio
async def test_start_time_flag_still_refreshes(progress):
    """The start-time flag refreshes even when the handler returns nothing."""
    queue = FakeQueue(["A", "B"])
    scaler, tasks, _ = start_worker(queue, {"A": None}, config={"refresh_worker": True})
    try:
        assert await wait_for_exit(tasks)
        assert queue.taken == ["A"]
    finally:
        await stop(scaler, tasks)


@pytest.mark.asyncio
async def test_refresh_lets_jobs_already_running_finish(progress, monkeypatch):
    """With concurrency > 1, a refresh stops intake but still drains the jobs
    the worker already holds, and reports each one."""
    # a partly drained worker takes freed slots after this bound
    monkeypatch.setattr(rp_scale, "FULL_WAIT_SECONDS", 0.01)
    queue = FakeQueue(["A", "B", "C"])
    finished = []
    scaler, tasks, release = start_worker(
        queue, {"A": {"stopPod": True}}, concurrency=2
    )
    release["B"] = asyncio.Event()
    original = scaler.jobs_handler

    async def recording_handler(session, config, job):
        result = await original(session, config, job)
        finished.append(job["id"])
        return result

    scaler.jobs_handler = recording_handler
    try:
        for _ in range(100):
            if finished == ["A"]:
                break
            await asyncio.sleep(0.01)
        assert finished == ["A"]
        await asyncio.sleep(0.1)  # give get_jobs every chance to take C
        assert queue.taken == ["A", "B"]

        release["B"].set()
        assert await wait_for_exit(tasks)
        assert finished == ["A", "B"]
        assert queue.taken == ["A", "B"]
        assert [job["id"] for job in queue.queue] == ["C"]
    finally:
        await stop(scaler, tasks, release)


@pytest.mark.asyncio
async def test_handler_error_does_not_refresh(progress):
    """A jobs_handler that raises sent no result, so it requested no refresh."""
    queue = FakeQueue(["A", "B"])
    scaler, tasks, _ = start_worker(queue, {})
    errors = []

    async def failing_handler(_session, _config, job):
        if job["id"] == "A":
            errors.append(job["id"])
            raise RuntimeError("boom")
        return {"output": "ok"}

    scaler.jobs_handler = failing_handler
    try:
        await asyncio.wait_for(queue.polled_empty.wait(), timeout=DEADLINE)
        assert errors == ["A"]
        assert queue.taken == ["A", "B"]
        assert scaler.is_alive()
    finally:
        await stop(scaler, tasks)


# ---------------------------------------------------------------------------
# rp_job.handle_job returns the result it sent
# ---------------------------------------------------------------------------


def _generator_handler(_job):
    yield "partial"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler, config, expected",
    [
        pytest.param(
            lambda _job: {"mesh": "done", "refresh_worker": True},
            {},
            {"output": {"mesh": "done"}, "stopPod": True},
            id="per-job-refresh",
        ),
        pytest.param(
            lambda _job: {"refresh_worker": True},
            {},
            {"stopPod": True},
            id="per-job-refresh-no-output",
        ),
        pytest.param(
            lambda _job: {"mesh": "done", "refresh_worker": False},
            {},
            {"output": {"mesh": "done"}},
            id="per-job-refresh-false",
        ),
        pytest.param(
            lambda _job: {"mesh": "done"},
            {},
            {"output": {"mesh": "done"}},
            id="no-refresh",
        ),
        pytest.param(
            lambda _job: "plain",
            {"refresh_worker": True},
            {"output": "plain", "stopPod": True},
            id="start-time-flag",
        ),
        pytest.param(
            _generator_handler,
            {"refresh_worker": True},
            {"output": [], "stopPod": True},
            id="generator-start-time-flag",
        ),
    ],
)
async def test_handle_job_returns_the_result_it_sent(handler, config, expected):
    with patch.object(rp_job, "send_result", AsyncMock()) as send_result, patch.object(
        rp_job, "stream_result", AsyncMock()
    ):
        result = await rp_job.handle_job(
            AsyncMock(), {"handler": handler, **config}, generate_job("A")
        )

    assert result == expected
    send_result.assert_awaited_once()
    assert send_result.await_args.args[1] is result


@pytest.mark.asyncio
async def test_handle_job_returns_stop_pod_when_handler_raises_with_start_time_flag():
    def failing(_job):
        raise RuntimeError("boom")

    with patch.object(rp_job, "send_result", AsyncMock()):
        result = await rp_job.handle_job(
            AsyncMock(), {"handler": failing, "refresh_worker": True}, generate_job("A")
        )

    assert result["stopPod"] is True
    assert "boom" in result["error"]


# ---------------------------------------------------------------------------
# A fake ai-api, spoken to over real HTTP
# ---------------------------------------------------------------------------


class FakeAiApi:
    """Serves job-take, job-done, job-stop and ping like ai-api, and records
    every request so a test can tell whether a job would have been lost."""

    def __init__(self, jobs):
        self.queue = list(jobs)
        self.takes: list[str | None] = []  # job id handed out, None for empty
        self.done: dict[str, dict[str, Any]] = {}
        self.stop_pod_received = False
        self.requests_after_stop_pod: list[str] = []
        self.polled_empty = asyncio.Event()
        self.port = 0
        self._runner = None

    def _record(self, request):
        if self.stop_pod_received and "/job-take" in request.path:
            self.requests_after_stop_pod.append(request.path)

    async def job_take(self, request):
        self._record(request)
        if not self.queue:
            self.takes.append(None)
            self.polled_empty.set()
            await asyncio.sleep(0.05)
            return web.Response(status=204)
        job = self.queue.pop(0)
        self.takes.append(job["id"])
        return web.json_response(job)

    async def job_done(self, request):
        payload = json.loads(await request.text())
        self.done[request.match_info["job_id"]] = payload
        if payload.get("stopPod"):
            self.stop_pod_received = True
        return web.Response(status=200)

    async def job_stop(self, _request):
        await asyncio.sleep(0.05)
        return web.Response(status=204)

    async def ping(self, _request):
        return web.Response(status=200)

    async def start(self):
        app = web.Application()
        app.router.add_get("/v2/{endpoint}/job-take/{pod}", self.job_take)
        app.router.add_post("/v2/{endpoint}/job-done/{pod}/{job_id}", self.job_done)
        app.router.add_get("/v2/{endpoint}/job-stop/{pod}", self.job_stop)
        app.router.add_get("/v2/{endpoint}/ping/{pod}", self.ping)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]

    async def close(self):
        await self._runner.cleanup()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}/v2/test-endpoint"

    @property
    def job_take_url(self):
        return f"{self.base}/job-take/$ID?gpu=test"

    @property
    def job_done_url(self):
        return f"{self.base}/job-done/$RUNPOD_POD_ID/$ID?gpu=test"


async def start_fake_ai_api(jobs) -> FakeAiApi:
    server = FakeAiApi(jobs)
    await server.start()
    return server


@pytest.fixture
async def http_worker(monkeypatch):
    """Point the SDK's real job-take/job-done/job-stop calls at a fake ai-api."""
    servers = []

    async def make(jobs):
        server = await start_fake_ai_api(jobs)
        servers.append(server)
        monkeypatch.setattr(rp_job, "JOB_GET_URL", server.job_take_url.replace("$ID", "pod-1"))
        monkeypatch.setattr(
            rp_http, "JOB_DONE_URL", server.job_done_url.replace("$RUNPOD_POD_ID", "pod-1")
        )
        return server

    yield make
    for server in servers:
        await server.close()


def _sync_handler(job):
    return {"echo": job["input"]["n"], "refresh_worker": job["input"].get("refresh", False)}


async def _async_handler(job):
    await asyncio.sleep(0.01)
    return {"echo": job["input"]["n"], "refresh_worker": job["input"].get("refresh", False)}


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [_sync_handler, _async_handler], ids=["sync", "async"])
async def test_http_per_job_refresh_leaves_next_job_queued(http_worker, handler):
    server = await http_worker(
        [generate_job("A", n=1, refresh=True), generate_job("B", n=2)]
    )
    scaler = rp_scale.JobScaler({"handler": handler})
    run = asyncio.create_task(scaler.run())
    try:
        _, pending = await asyncio.wait({run}, timeout=DEADLINE)
        assert server.takes == ["A"], "job B was taken after stopPod and would be lost"
        assert server.requests_after_stop_pod == []
        assert [job["id"] for job in server.queue] == ["B"]
        assert server.done == {"A": {"output": {"echo": 1}, "stopPod": True}}
        assert not pending, "the worker did not exit after requesting a refresh"
    finally:
        scaler.kill_worker()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


@pytest.mark.asyncio
async def test_http_start_time_flag_leaves_next_job_queued(http_worker):
    server = await http_worker([generate_job("A", n=1), generate_job("B", n=2)])
    scaler = rp_scale.JobScaler({"handler": _sync_handler, "refresh_worker": True})
    run = asyncio.create_task(scaler.run())
    try:
        _, pending = await asyncio.wait({run}, timeout=DEADLINE)
        assert server.takes == ["A"]
        assert server.done == {"A": {"output": {"echo": 1}, "stopPod": True}}
        assert not pending
    finally:
        scaler.kill_worker()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


@pytest.mark.asyncio
async def test_http_without_refresh_serves_the_whole_queue(http_worker):
    server = await http_worker(
        [generate_job("A", n=1), generate_job("B", n=2), generate_job("C", n=3)]
    )
    scaler = rp_scale.JobScaler({"handler": _sync_handler})
    run = asyncio.create_task(scaler.run())
    try:
        await asyncio.wait_for(server.polled_empty.wait(), timeout=DEADLINE)
        assert server.takes[:3] == ["A", "B", "C"]
        assert server.done == {
            "A": {"output": {"echo": 1}},
            "B": {"output": {"echo": 2}},
            "C": {"output": {"echo": 3}},
        }
        assert not run.done(), "a worker without refresh must keep running"
    finally:
        scaler.kill_worker()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


@pytest.mark.asyncio
async def test_http_refresh_on_a_later_job(http_worker):
    server = await http_worker(
        [
            generate_job("A", n=1),
            generate_job("B", n=2, refresh=True),
            generate_job("C", n=3),
        ]
    )
    scaler = rp_scale.JobScaler({"handler": _sync_handler})
    run = asyncio.create_task(scaler.run())
    try:
        _, pending = await asyncio.wait({run}, timeout=DEADLINE)
        assert server.takes == ["A", "B"]
        assert server.requests_after_stop_pod == []
        assert server.done["B"]["stopPod"] is True
        assert "stopPod" not in server.done["A"]
        assert not pending
    finally:
        scaler.kill_worker()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


# ---------------------------------------------------------------------------
# A real worker process against the fake ai-api
# ---------------------------------------------------------------------------

WORKER_SCRIPT = textwrap.dedent(
    """
    import runpod

    def handler(job):
        refresh = job["input"].get("refresh", False)
        return {"echo": job["input"]["n"], "refresh_worker": refresh}

    runpod.serverless.start({"handler": handler, "refresh_worker": %s})
    """
)


async def run_worker_process(tmp_path, server, start_time_flag=False, timeout=30):
    """Start a worker the way the platform does; return its exit code, or
    None if it was still running at the timeout (it is killed then)."""
    script = tmp_path / "worker.py"
    script.write_text(WORKER_SCRIPT % start_time_flag)
    env = {
        **os.environ,
        "RUNPOD_POD_ID": "pod-1",
        "RUNPOD_WEBHOOK_GET_JOB": server.job_take_url,
        "RUNPOD_WEBHOOK_POST_OUTPUT": server.job_done_url,
        "RUNPOD_WEBHOOK_POST_STREAM": f"{server.base}/job-stream/$RUNPOD_POD_ID/$ID?gpu=test",
        "RUNPOD_WEBHOOK_PING": f"{server.base}/ping/$RUNPOD_POD_ID?gpu=test",
        "RUNPOD_AI_API_KEY": "test-key",
        "RUNPOD_PING_INTERVAL": "1000",
        # host GPU/memory probes are covered elsewhere and vary by machine
        "RUNPOD_SKIP_FITNESS_CHECKS": "true",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        env=env,
        cwd=str(tmp_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        return await asyncio.wait_for(process.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return None


@pytest.mark.asyncio
async def test_process_per_job_refresh_exits_without_taking_next_job(tmp_path):
    server = await start_fake_ai_api(
        [generate_job("A", n=1, refresh=True), generate_job("B", n=2)]
    )
    try:
        returncode = await run_worker_process(tmp_path, server)
        assert server.takes == ["A"], "job B was taken after stopPod and would be lost"
        assert server.requests_after_stop_pod == []
        assert [job["id"] for job in server.queue] == ["B"]
        assert server.done == {"A": {"output": {"echo": 1}, "stopPod": True}}
        assert returncode == 0
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_process_start_time_flag_exits_without_taking_next_job(tmp_path):
    server = await start_fake_ai_api([generate_job("A", n=1), generate_job("B", n=2)])
    try:
        returncode = await run_worker_process(tmp_path, server, start_time_flag=True)
        assert server.takes == ["A"]
        assert server.done == {"A": {"output": {"echo": 1}, "stopPod": True}}
        assert returncode == 0
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_process_without_refresh_keeps_serving(tmp_path):
    server = await start_fake_ai_api([generate_job("A", n=1), generate_job("B", n=2)])
    try:
        returncode = await run_worker_process(tmp_path, server, timeout=8)
        assert returncode is None, "a worker without refresh must keep running"
        assert server.takes[:2] == ["A", "B"]
        assert server.done == {
            "A": {"output": {"echo": 1}},
            "B": {"output": {"echo": 2}},
        }
    finally:
        await server.close()
