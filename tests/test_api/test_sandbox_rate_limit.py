"""Sandbox requests and REST clients coordinate through the same quota buckets."""

import asyncio
from concurrent.futures import Future

import pytest
from aiohttp import web

from runpod import AsyncioSandbox
from runpod.api import rest, sandboxes
from runpod.api._rate_limit import rate_limits
from runpod.api.sandboxes import AsyncSandboxAPI
from runpod.error import RateLimitError
from tests.test_sandbox import sandbox_peer


@pytest.fixture
def peer(monkeypatch):
    assert rest.rate_limits is rate_limits
    assert sandboxes._open_rest_response_async is rest._open_rest_response_async
    monkeypatch.setattr(rate_limits, "buckets", {})
    with sandbox_peer() as service:
        monkeypatch.setenv("RUNPOD_API_BASE_URL", service.options["base_url"])
        yield service


@pytest.mark.parametrize("sync_rest", [False, True])
async def test_sandbox_rejection_blocks_sync_and_async_rest(peer, sync_rest):
    record = peer.create_record({})
    sandbox = await AsyncioSandbox.get(record["id"], **peer.options)
    try:
        peer.exec_responses.append(
            web.json_response(
                {"detail": "shared quota", "errors": ["quota"]},
                status=429,
                headers={"Retry-After": "60"},
            )
        )
        with pytest.raises(RateLimitError) as raised:
            await sandbox.exec(["side-effect"])
        assert raised.value.retry_after == 60
        assert raised.value.errors == ["quota"]
        assert peer.executed == [["side-effect"]]
        request_count = len(peer.requests)
        options = {"api_key": peer.options["api_key"], "max_rate_limit_wait": 0}
        with pytest.raises(RateLimitError):
            if sync_rest:
                await asyncio.to_thread(
                    rest.run_rest_request, "GET", "/v2/sandboxes", **options
                )
            else:
                await rest.run_rest_request_async("GET", "/v2/sandboxes", **options)
        assert len(peer.requests) == request_count
    finally:
        await sandbox.close()
    assert all(
        bucket.in_flight == bucket.users == 0 for bucket in rate_limits.buckets.values()
    )


@pytest.mark.parametrize("sync_rest", [False, True])
@pytest.mark.parametrize("operation", ["refresh", "logs"])
async def test_rest_rejection_blocks_sandbox_requests_and_handshakes(
    peer, sync_rest, operation
):
    record = peer.create_record({})
    sandbox = await AsyncioSandbox.get(record["id"], **peer.options)
    try:
        peer.exec_responses.append(
            web.json_response(
                {"detail": "shared quota"}, status=429, headers={"Retry-After": "60"}
            )
        )
        options = {
            "api_key": peer.options["api_key"],
            "json": {"command": ["side-effect"]},
            "max_retries": 0,
        }
        path = f"/v2/sandboxes/{sandbox.id}/exec"
        with pytest.raises(RateLimitError):
            if sync_rest:
                await asyncio.to_thread(rest.run_rest_request, "POST", path, **options)
            else:
                await rest.run_rest_request_async("POST", path, **options)
        request_count = len(peer.requests)
        with pytest.raises(RateLimitError) as raised:
            if operation == "logs":
                await sandbox.logs().open()
            else:
                await sandbox.refresh()
        assert raised.value.retry_after > sandbox._request_timeout
        assert len(peer.requests) == request_count
        assert peer.executed == [["side-effect"]]
    finally:
        await sandbox.close()


async def test_successful_sandbox_response_updates_shared_quota(peer):
    record = peer.create_record({})
    sandbox = await AsyncioSandbox.get(record["id"], **peer.options)
    try:
        peer.exec_responses.append(
            web.json_response(
                {"output": "completed"}, headers={"RateLimit": '"minute";r=0;t=60'}
            )
        )
        result = await sandbox.exec(["work"])
        assert result.output == "completed"
        request_count = len(peer.requests)
        with pytest.raises(RateLimitError):
            await rest.run_rest_request_async(
                "GET",
                "/v2/sandboxes",
                api_key=peer.options["api_key"],
                max_rate_limit_wait=0,
            )
        assert len(peer.requests) == request_count
    finally:
        await sandbox.close()


async def test_sandbox_log_rejection_updates_shared_quota(peer):
    record = peer.create_record({})
    sandbox = await AsyncioSandbox.get(record["id"], **peer.options)
    try:
        peer.log_statuses.append(429)
        with pytest.raises(RateLimitError) as raised:
            await sandbox.logs().open()
        assert raised.value.retry_after > 0
        request_count = len(peer.requests)
        with pytest.raises(RateLimitError):
            await rest.run_rest_request_async(
                "GET",
                "/v2/sandboxes",
                api_key=peer.options["api_key"],
                max_rate_limit_wait=0,
            )
        assert len(peer.requests) == request_count
        assert sum(path.endswith("/logs") for _, path, _, _ in peer.requests) == 1
    finally:
        await sandbox.close()


async def test_open_log_stream_does_not_hold_quota_reservation(peer):
    peer.hold_logs = True
    peer.log_bytes = peer.log_bytes.split(b"event: timeout")[0]
    record = peer.create_record({})
    sandbox = await AsyncioSandbox.get(record["id"], **peer.options)
    try:
        async with sandbox.logs() as logs:
            event = await anext(logs)
            assert event.line == "café"
            assert all(
                bucket.in_flight == bucket.users == 0
                for bucket in rate_limits.buckets.values()
            )
            response = await rest.run_rest_request_async(
                "GET", f"/v2/sandboxes/{sandbox.id}", api_key=peer.options["api_key"]
            )
            assert response["id"] == sandbox.id
        assert await asyncio.to_thread(peer.log_disconnected.wait, 2)
    finally:
        await sandbox.close()


async def test_cancelling_queued_sandbox_request_releases_waiter(peer, monkeypatch):
    api = AsyncSandboxAPI(
        **{
            key: value
            for key, value in peer.options.items()
            if key != "startup_timeout"
        }
    )
    queued = asyncio.Event()
    try:
        with rate_limits.bucket(
            peer.options["base_url"], peer.options["api_key"]
        ) as bucket:
            assert bucket.acquire() == 0
            acquire = bucket.acquire

            def observe_acquire(budget):
                result = acquire(budget)
                if isinstance(result, Future):
                    queued.set()
                return result

            monkeypatch.setattr(bucket, "acquire", observe_acquire)
            task = asyncio.create_task(api.list())
            try:
                await asyncio.wait_for(queued.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not bucket.waiters
                assert bucket.in_flight == 1
                assert peer.requests == []
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                bucket.release()
            assert await api.list() == []
            assert bucket.in_flight == 0
    finally:
        await api.close()


@pytest.mark.parametrize("isolation", ["key", "origin"])
async def test_sandbox_quota_is_scoped_to_origin_and_key(peer, isolation):
    origin = peer.options["base_url"] if isolation == "key" else "https://other.invalid"
    key = "other-key" if isolation == "key" else peer.options["api_key"]
    with rate_limits.bucket(origin, key) as bucket:
        bucket.update(429, {"Retry-After": "60"}, 0)
    api = AsyncSandboxAPI(
        api_key=peer.options["api_key"], base_url=peer.options["base_url"]
    )
    try:
        assert await api.list() == []
        assert len(peer.requests) == 1
    finally:
        await api.close()


async def test_sandbox_waits_for_available_shared_quota(peer, monkeypatch):
    api = AsyncSandboxAPI(
        api_key=peer.options["api_key"], base_url=peer.options["base_url"]
    )
    delays = []
    try:
        with rate_limits.bucket(
            peer.options["base_url"], peer.options["api_key"]
        ) as bucket:
            acquire = bucket.acquire

            def observe_acquire(budget):
                result = acquire(budget)
                if isinstance(result, float) and result > 0:
                    delays.append(result)
                    assert peer.requests == []
                return result

            monkeypatch.setattr(bucket, "acquire", observe_acquire)
            bucket.update(429, {"Retry-After": "0.05"}, 0)
            assert await api.list() == []
            assert delays
            assert len(peer.requests) == 1
            assert bucket.in_flight == 0
    finally:
        await api.close()
