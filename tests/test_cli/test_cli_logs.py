import asyncio
import json
from types import SimpleNamespace

import aiohttp
import click
import pytest

from runpod.rp_cli import logs


def event(line="hello", ts="2026-01-01T00:00:00Z"):
    return {"source": "container", "line": line, "ts": ts}


@pytest.mark.parametrize("as_json", [False, True])
def test_pod_snapshot_uses_sdk_reader(monkeypatch, capsys, as_json):
    calls = []

    async def stream(pod_id, **kwargs):
        calls.append((pod_id, kwargs))
        yield event()

    monkeypatch.setattr(logs, "stream_pod_logs", stream)
    logs.show_logs("pod1", tail=7, since="2025-01-01T00:00:00Z", as_json=as_json)
    text = capsys.readouterr().out
    assert json.loads(text) == event() if as_json else text == "[container] hello\n"
    assert calls[0][0] == "pod1"
    assert calls[0][1]["log_type"] is None
    assert calls[0][1]["tail"] == 7
    assert calls[0][1]["since"] == "2025-01-01T00:00:00Z"
    assert calls[0][1]["idle_timeout"] == logs.SNAPSHOT_IDLE_SECONDS


def test_worker_snapshot_is_sorted_and_filterable(monkeypatch, capsys):
    monkeypatch.setattr(
        logs,
        "run_rest_request",
        lambda *a, **k: {"workers": [{"id": "a"}, {"id": "b"}, {"id": "ignored"}]},
    )

    async def stream(worker, **kwargs):
        assert kwargs["endpoint_id"] == "ep1"
        assert kwargs["log_type"] == "system"
        yield event(
            worker, "2026-01-01T00:00:02Z" if worker == "a" else "2026-01-01T00:00:01Z"
        )

    monkeypatch.setattr(logs, "stream_pod_logs", stream)
    logs.show_logs(
        endpoint_id="ep1", worker_ids=("a", "b"), source="system", as_json=True
    )
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["worker"] for row in rows] == ["b", "a"]


@pytest.mark.parametrize("empty", [False, True])
def test_snapshot_closes_idle_or_live_stream(monkeypatch, capsys, empty):
    closed = []
    monkeypatch.setattr(logs, "SNAPSHOT_IDLE_SECONDS", 0.01)

    async def stream(*args, **kwargs):
        try:
            if not empty:
                yield event("backfill")
                yield event("live", "2999-01-01T00:00:00Z")
            await asyncio.sleep(kwargs["idle_timeout"])
        finally:
            closed.append(True)

    monkeypatch.setattr(logs, "stream_pod_logs", stream)
    logs.show_logs("pod1")
    assert closed == [True]
    assert capsys.readouterr().out == ("" if empty else "[container] backfill\n")


def test_empty_workers(monkeypatch, capsys):
    monkeypatch.setattr(logs, "run_rest_request", lambda *a, **k: {"workers": []})
    logs.show_logs(endpoint_id="ep1")
    assert "no workers" in capsys.readouterr().out


def test_snapshot_propagates_connection_timeout(monkeypatch):
    async def stream(*args, **kwargs):
        raise asyncio.TimeoutError("connection timed out")
        yield

    monkeypatch.setattr(logs, "stream_pod_logs", stream)
    with pytest.raises(click.ClickException, match="connection timed out"):
        logs.show_logs("pod1")


def test_snapshot_surfaces_delayed_authentication_failure(monkeypatch):
    monkeypatch.setattr(logs, "SNAPSHOT_IDLE_SECONDS", 0.001)

    async def stream(*args, **kwargs):
        await asyncio.sleep(0.03)
        raise aiohttp.ClientResponseError(
            SimpleNamespace(real_url="https://api.runpod.io/v2/pods/pod1/logs"),
            (),
            status=403,
            message="denied",
        )
        yield

    monkeypatch.setattr(logs, "stream_pod_logs", stream)
    with pytest.raises(click.ClickException, match="denied"):
        logs.show_logs("pod1")


@pytest.mark.parametrize(
    "endpoint_id,status,raises",
    [(None, 404, True), ("ep1", 404, False), ("ep1", 403, True)],
)
def test_stream_errors_do_not_hide_auth_failures(
    monkeypatch, endpoint_id, status, raises
):
    monkeypatch.setattr(
        logs, "run_rest_request", lambda *a, **k: {"workers": [{"id": "pod1"}]}
    )

    async def stream(*args, **kwargs):
        raise aiohttp.ClientResponseError(
            SimpleNamespace(real_url="https://api.runpod.io/v2/pods/pod1/logs"),
            (),
            status=status,
            message="denied",
        )
        yield

    monkeypatch.setattr(logs, "stream_pod_logs", stream)
    if raises:
        with pytest.raises(click.ClickException):
            logs.show_logs(None if endpoint_id else "pod1", endpoint_id=endpoint_id)
    else:
        logs.show_logs(endpoint_id=endpoint_id)


async def test_follow_resumes_and_cancels_streams(monkeypatch, capsys):
    resumes, closed = [], []
    received = asyncio.Event()
    monkeypatch.setattr(logs, "RECONNECT_SECONDS", 0)
    monkeypatch.setattr(logs, "WORKER_POLL_SECONDS", 0.01)

    async def stream(worker, **kwargs):
        assert kwargs["idle_timeout"] is None
        resumes.append(kwargs["last_event_id"])
        try:
            if len(resumes) == 1:
                kwargs["on_event_id"]("cursor1")
                yield event("first")
            else:
                yield event("second")
                received.set()
                await asyncio.sleep(10)
        finally:
            closed.append(True)

    monkeypatch.setattr(logs, "stream_pod_logs", stream)
    task = asyncio.create_task(
        logs._show_logs("pod1", None, (), True, None, 10, None, False)
    )
    await asyncio.wait_for(received.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert resumes == [None, "cursor1"]
    assert len(closed) == 2
    assert "[container] first\n[container] second\n" == capsys.readouterr().out


async def test_follow_attaches_new_workers(monkeypatch, capsys):
    polls = 0
    received = asyncio.Event()
    monkeypatch.setattr(logs, "WORKER_POLL_SECONDS", 0.01)

    def workers(*args):
        nonlocal polls
        polls += 1
        return {"workers": [] if polls == 1 else [{"id": "new"}]}

    async def stream(worker, **kwargs):
        yield event(worker)
        received.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(logs, "run_rest_request", workers)
    monkeypatch.setattr(logs, "stream_pod_logs", stream)
    task = asyncio.create_task(
        logs._show_logs(None, "ep1", (), True, None, 10, None, False)
    )
    await asyncio.wait_for(received.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "new [container] new" in capsys.readouterr().out


@pytest.mark.parametrize("endpoint_id", [None, "ep1"])
@pytest.mark.parametrize("status", [429, 500, 502, 503, 599])
async def test_follow_retries_transient_http_errors(
    monkeypatch, capsys, endpoint_id, status
):
    resumes, closed = [], []
    received = asyncio.Event()
    companion_started = asyncio.Event()
    monkeypatch.setattr(logs, "RECONNECT_SECONDS", 0.001)
    monkeypatch.setattr(logs, "WORKER_POLL_SECONDS", 0.001)
    monkeypatch.setattr(
        logs,
        "run_rest_request",
        lambda *a, **k: {"workers": [{"id": "pod1"}, {"id": "pod2"}]},
    )

    async def stream(worker, **kwargs):
        try:
            if worker == "pod2":
                companion_started.set()
                yield event("companion")
                await asyncio.Event().wait()
                return
            resumes.append(kwargs["last_event_id"])
            if len(resumes) == 1:
                if endpoint_id:
                    await companion_started.wait()
                kwargs["on_event_id"]("cursor1")
                yield event("first")
            elif len(resumes) == 2:
                raise aiohttp.ClientResponseError(
                    SimpleNamespace(real_url="https://api.runpod.io/v2/pods/pod1/logs"),
                    (),
                    status=status,
                    message="temporarily unavailable",
                )
            else:
                yield event("resumed")
                received.set()
                await asyncio.Event().wait()
        finally:
            closed.append(worker)

    monkeypatch.setattr(logs, "stream_pod_logs", stream)
    task = asyncio.create_task(
        logs._show_logs(
            None if endpoint_id else "pod1", endpoint_id, (), True, None, 10, None, True
        )
    )
    try:
        await asyncio.wait_for(received.wait(), 1)
        assert not task.done()
        assert resumes == [None, "cursor1", "cursor1"]
        assert closed == ["pod1", "pod1"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert closed.count("pod1") == 3
    assert closed.count("pod2") == (1 if endpoint_id else 0)
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["line"] for row in rows] == (
        ["companion", "first", "resumed"] if endpoint_id else ["first", "resumed"]
    )


@pytest.mark.parametrize("endpoint_id", [None, "ep1"])
@pytest.mark.parametrize(
    "follow,status",
    [(True, 400), (True, 401), (True, 403), (False, 429), (False, 500), (False, 503)],
)
def test_http_errors_that_must_surface(monkeypatch, endpoint_id, follow, status):
    monkeypatch.setattr(logs, "WORKER_POLL_SECONDS", 0.001)
    monkeypatch.setattr(
        logs, "run_rest_request", lambda *a, **k: {"workers": [{"id": "pod1"}]}
    )

    async def stream(*args, **kwargs):
        raise aiohttp.ClientResponseError(
            SimpleNamespace(real_url="https://api.runpod.io/v2/pods/pod1/logs"),
            (),
            status=status,
            message="request failed",
        )
        yield

    monkeypatch.setattr(logs, "stream_pod_logs", stream)
    with pytest.raises(click.ClickException, match="request failed"):
        logs.show_logs(
            None if endpoint_id else "pod1", endpoint_id=endpoint_id, follow=follow
        )
