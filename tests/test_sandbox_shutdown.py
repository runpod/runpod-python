"""Threaded shutdown closes admission before releasing loop-owned sessions."""

import asyncio
import threading
from concurrent.futures import Future

import pytest

from runpod import Sandbox
from tests.test_sandbox import peer


@pytest.mark.parametrize("operation", ["close", "terminate", "owned_exit", "borrowed_exit"])
def test_shutdown_rejects_requests_after_session_close(peer, monkeypatch, operation):
    sandbox = Sandbox.create(**peer.options)
    if operation == "borrowed_exit":
        sandbox.close()
        sandbox = Sandbox.get(sandbox.id, **peer.options)
    if operation.endswith("exit"):
        sandbox.__enter__()
    session = sandbox._sandbox._api._session
    loop = sandbox._runner._loop
    session_closed = threading.Event()
    release_shutdown = threading.Event()
    outcome = Future()
    original_close = sandbox._sandbox.close

    async def gated_close():
        await original_close()
        session_closed.set()
        assert await asyncio.to_thread(release_shutdown.wait, 5)

    monkeypatch.setattr(sandbox._sandbox, "close", gated_close)

    def shutdown():
        try:
            if operation.endswith("exit"):
                sandbox.__exit__(None, None, None)
            else:
                getattr(sandbox, operation)()
            outcome.set_result(None)
        except BaseException as error:
            outcome.set_exception(error)

    worker = threading.Thread(target=shutdown, daemon=True)
    worker.start()
    try:
        assert session_closed.wait(3)
        assert session.closed
        assert not loop.is_closed()
        with pytest.raises(RuntimeError, match="shutting down"):
            sandbox.refresh()
    finally:
        release_shutdown.set()
        worker.join(5)
        monkeypatch.setattr(sandbox._sandbox, "close", original_close)
        sandbox.close()
    assert not worker.is_alive()
    outcome.result(timeout=0)
    assert loop.is_closed()
    assert not sandbox._runner.started
    try:
        assert sandbox.refresh().state == (
            "TERMINATED" if operation in ("terminate", "owned_exit") else "RUNNING"
        )
        assert sandbox._sandbox._api._session is not session
    finally:
        sandbox.terminate()


@pytest.mark.parametrize("operation", ["close", "terminate", "exit"])
def test_shutdown_drains_requests_before_closing_session(peer, monkeypatch, operation):
    sandbox = Sandbox.create(**peer.options)
    if operation == "exit":
        sandbox.__enter__()
    session = sandbox._sandbox._api._session
    started = threading.Event()
    cancelled = threading.Event()
    release_request = threading.Event()
    close_started = threading.Event()
    refreshed, closed = Future(), Future()
    original_refresh = sandbox._sandbox.refresh
    original_close = sandbox._sandbox.close

    async def draining_refresh():
        try:
            started.set()
            await asyncio.Future()
        finally:
            cancelled.set()
            assert await asyncio.to_thread(release_request.wait, 5)
            await original_refresh()

    async def tracked_close():
        close_started.set()
        await original_close()

    monkeypatch.setattr(sandbox._sandbox, "refresh", draining_refresh)
    monkeypatch.setattr(sandbox._sandbox, "close", tracked_close)

    def refresh():
        try:
            refreshed.set_result(sandbox.refresh())
        except BaseException as error:
            refreshed.set_exception(error)

    def close():
        try:
            if operation == "exit":
                sandbox.__exit__(None, None, None)
            else:
                getattr(sandbox, operation)()
            closed.set_result(None)
        except BaseException as error:
            closed.set_exception(error)

    reader = threading.Thread(target=refresh, daemon=True)
    closer = threading.Thread(target=close, daemon=True)
    reader.start()
    try:
        assert started.wait(3)
        closer.start()
        assert cancelled.wait(3)
        assert not close_started.is_set()
        assert not session.closed
        with pytest.raises(RuntimeError, match="shutting down"):
            sandbox.refresh()
    finally:
        release_request.set()
        reader.join(5)
        if closer.ident is not None:
            closer.join(5)
        monkeypatch.setattr(sandbox._sandbox, "refresh", original_refresh)
        monkeypatch.setattr(sandbox._sandbox, "close", original_close)
        sandbox.close()
    assert not reader.is_alive()
    assert not closer.is_alive()
    with pytest.raises(asyncio.CancelledError):
        refreshed.result(timeout=0)
    closed.result(timeout=0)
    assert close_started.is_set()
    assert session.closed
    assert session._loop.is_closed()
    try:
        assert sandbox.refresh().state == (
            "RUNNING" if operation == "close" else "TERMINATED"
        )
    finally:
        sandbox.terminate()


def test_termination_waits_for_concurrent_close(peer, monkeypatch):
    sandbox = Sandbox.create(**peer.options)
    closing = threading.Event()
    release_close = threading.Event()
    terminating = threading.Event()
    closed, terminated = Future(), Future()
    original_close = sandbox._sandbox.close

    async def gated_close():
        closing.set()
        assert await asyncio.to_thread(release_close.wait, 5)
        await original_close()

    monkeypatch.setattr(sandbox._sandbox, "close", gated_close)

    def close():
        try:
            sandbox.close()
            closed.set_result(None)
        except BaseException as error:
            closed.set_exception(error)

    def terminate():
        try:
            sandbox.terminate()
            terminated.set_result(None)
        except BaseException as error:
            terminated.set_exception(error)

    closer = threading.Thread(target=close, daemon=True)
    terminator = threading.Thread(target=terminate, daemon=True)
    closer.start()
    try:
        assert closing.wait(3)
        original_wait = sandbox._runner._stopped.wait

        def tracked_wait(*args, **kwargs):
            terminating.set()
            return original_wait(*args, **kwargs)

        monkeypatch.setattr(sandbox._runner._stopped, "wait", tracked_wait)
        terminator.start()
        assert terminating.wait(3)
        assert not terminated.done()
    finally:
        release_close.set()
        closer.join(5)
        if terminator.ident is not None:
            terminator.join(5)
        monkeypatch.setattr(sandbox._sandbox, "close", original_close)
        sandbox.close()
    assert not closer.is_alive()
    assert not terminator.is_alive()
    closed.result(timeout=0)
    terminated.result(timeout=0)
    assert peer.records[sandbox.id]["state"] == "TERMINATED"
    assert not sandbox._runner.started
