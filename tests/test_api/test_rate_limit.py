"""REST quota parsing, reservations, and bounded retries."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest
import requests

from runpod.api import _rate_limit, rest
from runpod.error import QueryError, RateLimitError


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 100.0

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(_rate_limit.time, "monotonic", lambda: clock.now)
    return clock


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("2", 2),
        (" 0.25 ", 0.25),
        ("0", 0),
        ("-1", None),
        ("NaN", None),
        ("inf", None),
        ("1e309", None),
        ("garbage", None),
        ("", None),
    ],
)
def test_retry_after_seconds(value, expected):
    assert _rate_limit._retry_after(value) == expected


def test_retry_after_http_date(monkeypatch):
    monkeypatch.setattr(_rate_limit.time, "time", lambda: 100)
    future = format_datetime(datetime.fromtimestamp(105, timezone.utc), usegmt=True)
    past = format_datetime(datetime.fromtimestamp(90, timezone.utc), usegmt=True)
    assert _rate_limit._retry_after(future) == 5
    assert _rate_limit._retry_after(past) == 0


def test_parse_runpod_windows():
    assert _rate_limit._windows(
        '"minute";r=45;t=12, "hour";t=1812;r=2800, "day";r=49500;t=45012'
    ) == {'"minute"': (45, 12), '"hour"': (2800, 1812), '"day"': (49500, 45012)}


@pytest.mark.parametrize(
    "value",
    [
        "",
        "garbage",
        '"minute";r=0',
        '"minute";r=-1;t=30',
        '"minute";r=0;t=NaN',
        '"minute";r=0;t=inf',
        '"minute";r=0;t=999999999999999999999999999',
        '"unknown";r=0;t=30',
    ],
)
def test_malformed_windows_are_ignored(value):
    assert _rate_limit._windows(value) == {}


def test_budget_reservations_and_single_reset_probe(clock):
    bucket = _rate_limit._Bucket()
    assert bucket.acquire() == 0
    bucket.update(200, {"RateLimit": '"minute";r=2;t=10'}, 0)
    bucket.release()
    assert bucket.acquire() == 0
    assert bucket.acquire() == 0
    assert bucket.acquire() == 10
    bucket.release()
    bucket.release()
    clock.sleep(10)
    assert bucket.acquire() == 0
    assert bucket.acquire() > 0
    bucket.update(200, {"RateLimit": '"minute";r=5;t=60'}, 0)
    bucket.release()
    assert bucket.acquire() == 0
    bucket.release()


def test_longest_exhausted_window_controls_wait(clock):
    bucket = _rate_limit._Bucket()
    assert bucket.acquire() == 0
    assert (
        bucket.update(
            429,
            {
                "Retry-After": "1",
                "RateLimit": '"minute";r=0;t=10, "hour";r=0;t=100, "day";r=50;t=1000',
            },
            0,
        )
        == 100
    )
    bucket.release()
    assert bucket.acquire() == 100


def test_out_of_order_success_cannot_restore_spent_quota_or_clear_cooldown(clock):
    bucket = _rate_limit._Bucket()
    bucket.update(200, {}, 0)
    assert bucket.acquire() == 0
    assert bucket.acquire() == 0
    bucket.update(429, {"Retry-After": "10", "RateLimit": '"minute";r=0;t=5'}, 0)
    bucket.release()
    bucket.update(200, {"RateLimit": '"minute";r=10;t=5'}, 0)
    bucket.release()
    assert bucket.acquire() == 10
    assert bucket.windows['"minute"'][0] == 0


def test_overlapping_cooldowns_only_extend(clock):
    bucket = _rate_limit._Bucket()
    bucket.update(429, {"Retry-After": "10"}, 0)
    clock.sleep(1)
    bucket.update(429, {"Retry-After": "2"}, 0)
    assert bucket.acquire() == 9
    bucket.update(429, {"Retry-After": "20"}, 0)
    assert bucket.acquire() == 20


def test_headerless_cooldown_allows_one_probe(clock):
    bucket = _rate_limit._Bucket()
    bucket.update(429, {"Retry-After": "1"}, 0)
    clock.sleep(1)
    assert bucket.acquire() == 0
    assert bucket.acquire() > 0
    bucket.update(200, {}, 0)
    bucket.release()
    assert bucket.acquire() == 0
    assert bucket.acquire() == 0
    bucket.release()
    bucket.release()


def test_expired_windows_without_response_headers_do_not_deadlock(clock):
    bucket = _rate_limit._Bucket()
    bucket.update(200, {"RateLimit": '"minute";r=0;t=1'}, 0)
    clock.sleep(1)
    assert bucket.acquire() == 0
    bucket.update(200, {}, 0)
    bucket.release()
    assert bucket.windows == {}


def test_in_flight_requests_are_subtracted_from_learned_budget(clock):
    bucket = _rate_limit._Bucket()
    bucket.update(200, {}, 0)
    assert bucket.acquire() == 0
    assert bucket.acquire() == 0
    bucket.update(200, {"RateLimit": '"minute";r=1;t=10'}, 0)
    bucket.release()
    assert bucket.acquire() == 10
    bucket.release()


def test_initial_discovery_allows_one_request_then_headerless_concurrency(clock):
    bucket = _rate_limit._Bucket()
    assert bucket.acquire() == 0
    assert bucket.acquire() > 0
    bucket.update(200, {}, 0)
    bucket.release()
    assert bucket.acquire() == 0
    assert bucket.acquire() == 0
    bucket.release()
    bucket.release()


def test_failed_discovery_releases_probe(clock):
    bucket = _rate_limit._Bucket()
    assert bucket.acquire() == 0
    bucket.release()
    assert bucket.acquire() == 0
    bucket.release()


def test_atomic_reservations_across_threads():
    bucket = _rate_limit._Bucket()
    bucket.update(200, {"RateLimit": '"minute";r=3;t=60'}, 0)
    with ThreadPoolExecutor(max_workers=10) as executor:
        delays = list(executor.map(lambda _: bucket.acquire(), range(20)))
    assert delays.count(0) == 3
    assert bucket.in_flight == 3
    for _ in range(3):
        bucket.release()


def test_registry_isolates_credentials_and_origins_but_shares_routes(clock):
    limits = _rate_limit._RateLimits()
    with limits.bucket("https://example.com/v2/pods", "key") as first:
        with limits.bucket("https://example.com/v2/templates?all=1", "key") as same:
            assert same is first
        with limits.bucket("https://example.com/v2/pods", "other") as other:
            assert other is not first
        with limits.bucket("http://example.com/v2/pods", "key") as other:
            assert other is not first
        with limits.bucket("https://other.example/v2/pods", "key") as other:
            assert other is not first
    assert all(isinstance(key[2], bytes) for key in limits.buckets)
    assert "key" not in repr(limits.buckets)


def test_registry_retains_active_cooldowns_and_evicts_idle_state(clock):
    limits = _rate_limit._RateLimits()
    with limits.bucket("https://example.com", "key") as active:
        with limits.bucket("https://example.com", "limited") as limited:
            limited.update(429, {"Retry-After": "500"}, 0)
        with limits.bucket("https://example.com", "idle") as idle:
            pass
        clock.sleep(301)
        with limits.bucket("https://example.com", "new"):
            assert active in limits.buckets.values()
            assert limited in limits.buckets.values()
            assert idle not in limits.buckets.values()
    clock.sleep(300)
    with limits.bucket("https://example.com", "new"):
        assert limited not in limits.buckets.values()


def test_credential_fingerprints_are_private_to_each_registry():
    first = _rate_limit._RateLimits()
    second = _rate_limit._RateLimits()
    with first.bucket("https://example.com", "key"):
        with second.bucket("https://example.com", "key"):
            assert next(iter(first.buckets)) != next(iter(second.buckets))


@pytest.mark.parametrize("attempt", [0, 1, 5, 1000])
def test_fallback_is_positive_jittered_and_capped(attempt, clock, monkeypatch):
    monkeypatch.setattr(_rate_limit.random, "uniform", lambda low, high: 0.75)
    bucket = _rate_limit._Bucket()
    assert bucket.update(429, {"Retry-After": "NaN"}, attempt) == (
        0.75 * 2 ** min(attempt, 6)
    )


@pytest.fixture
def quota_server(monkeypatch):
    state = {"requests": [], "responses": [], "lock": threading.Lock()}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            with state["lock"]:
                state["requests"].append(
                    (self.path, body, self.headers["Authorization"])
                )
                status, headers, payload = (
                    state["responses"].pop(0)
                    if state["responses"]
                    else (200, {}, {"id": "pod-1"})
                )
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "application/json")
            for key, value in headers.items():
                self.send_header(
                    key.replace("\r", "").replace("\n", ""),
                    value.replace("\r", "").replace("\n", ""),
                )
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_PUT = do_DELETE = do_POST

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("RUNPOD_API_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(rest, "rate_limits", _rate_limit._RateLimits())
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture(params=["sync", "async"])
def request_rest(request):
    if request.param == "sync":
        return rest.run_rest_request

    def run(*args, **kwargs):
        return asyncio.run(rest.run_rest_request_async(*args, **kwargs))

    return run


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
def test_retry_preserves_request(quota_server, request_rest, method):
    quota_server["responses"] = [
        (429, {"Retry-After": "0.01"}, {"detail": "slow down"})
    ]
    assert request_rest(
        method, "/v2/pods", params={"all": "true"}, json={"name": "test"}, api_key="key"
    ) == {"id": "pod-1"}
    assert len(quota_server["requests"]) == 2
    assert quota_server["requests"][0] == quota_server["requests"][1]
    assert quota_server["requests"][0][0] == "/v2/pods?all=true"


def test_retries_are_bounded_and_preserve_error_details(quota_server, request_rest):
    quota_server["responses"] = [
        (429, {"Retry-After": "0"}, {"detail": "slow down", "errors": ["quota"]})
    ] * 5
    with pytest.raises(RateLimitError) as raised:
        request_rest("POST", "/v2/pods", api_key="key")
    assert len(quota_server["requests"]) == 4
    assert isinstance(raised.value, QueryError)
    assert raised.value.status_code == 429
    assert raised.value.retry_after == 0
    assert raised.value.errors == ["quota"]
    assert raised.value.query == "POST /v2/pods"
    assert str(raised.value) == "slow down"


def test_zero_retries_surfaces_first_rejection(quota_server, request_rest):
    quota_server["responses"] = [(429, {"Retry-After": "1"}, {"detail": "slow down"})]
    with pytest.raises(RateLimitError):
        request_rest("GET", "/v2/pods", api_key="key", max_retries=0)
    assert len(quota_server["requests"]) == 1


def test_long_wait_fails_without_sleep_or_retry(quota_server, request_rest):
    quota_server["responses"] = [
        (429, {"Retry-After": "3600"}, {"detail": "hour quota"})
    ]
    with pytest.raises(RateLimitError) as raised:
        request_rest("POST", "/v2/pods", api_key="key", max_rate_limit_wait=1)
    assert raised.value.retry_after == 3600
    assert str(raised.value) == "hour quota"
    assert len(quota_server["requests"]) == 1


def test_wait_budget_is_cumulative_across_retries(quota_server, request_rest):
    quota_server["responses"] = [
        (429, {"Retry-After": "0.04"}, {"detail": "quota"})
    ] * 3
    with pytest.raises(RateLimitError) as raised:
        request_rest("GET", "/v2/pods", api_key="key", max_rate_limit_wait=0.06)
    assert raised.value.retry_after == 0.04
    assert len(quota_server["requests"]) == 2


@pytest.mark.parametrize("retry_header", [{}, {"Retry-After": "invalid"}])
def test_missing_or_malformed_retry_hint_uses_backoff(
    quota_server, request_rest, monkeypatch, retry_header
):
    monkeypatch.setattr(_rate_limit.random, "uniform", lambda low, high: 0.01)
    quota_server["responses"] = [(429, retry_header, {"detail": "quota"})]
    assert request_rest("GET", "/v2/pods", api_key="key") == {"id": "pod-1"}
    assert len(quota_server["requests"]) == 2


def test_sync_response_is_closed_before_sleep(monkeypatch, clock):
    first = requests.Response()
    first.status_code = 429
    first.headers["Retry-After"] = "1"
    first._content = b"quota"
    first._content_consumed = True
    first.close = Mock()
    second = requests.Response()
    second.status_code = 200
    second._content = b'{"id":"pod-1"}'
    second._content_consumed = True
    second.close = Mock()
    monkeypatch.setattr(rest, "rate_limits", _rate_limit._RateLimits())
    monkeypatch.setattr(rest.requests, "request", Mock(side_effect=[first, second]))

    def sleep(seconds):
        first.close.assert_called_once()
        clock.sleep(seconds)

    monkeypatch.setattr(rest.time, "sleep", sleep)
    assert rest.run_rest_request("GET", "/v2/pods", api_key="key") == {"id": "pod-1"}
    second.close.assert_called_once()


def test_successful_last_token_blocks_other_routes(quota_server, request_rest):
    quota_server["responses"] = [
        (200, {"RateLimit": '"minute";r=0;t=60'}, {"id": "pod-1"})
    ]
    assert request_rest("GET", "/v2/pods", api_key="key") == {"id": "pod-1"}
    with pytest.raises(RateLimitError) as raised:
        request_rest("GET", "/v2/templates", api_key="key", max_rate_limit_wait=0)
    assert 0 < raised.value.retry_after <= 60
    assert len(quota_server["requests"]) == 1
    assert request_rest("GET", "/v2/templates", api_key="other") == {"id": "pod-1"}


def test_sync_and_async_share_cooldown(quota_server):
    quota_server["responses"] = [(429, {"Retry-After": "60"}, {"detail": "quota"})]
    with pytest.raises(RateLimitError):
        rest.run_rest_request("GET", "/v2/pods", api_key="key", max_retries=0)
    with pytest.raises(RateLimitError):
        asyncio.run(
            rest.run_rest_request_async(
                "GET", "/v2/templates", api_key="key", max_rate_limit_wait=0
            )
        )
    assert len(quota_server["requests"]) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 503])
def test_other_errors_are_not_retried(quota_server, request_rest, status):
    from runpod.error import RunPodError

    quota_server["responses"] = [(status, {"Retry-After": "0"}, {"detail": "failure"})]
    with pytest.raises(RunPodError):
        request_rest("POST", "/v2/pods", api_key="key")
    assert len(quota_server["requests"]) == 1


@pytest.mark.parametrize(
    "options",
    [
        {"max_retries": -1},
        {"max_retries": 1.5},
        {"max_retries": True},
        {"max_rate_limit_wait": -1},
        {"max_rate_limit_wait": float("nan")},
        {"max_rate_limit_wait": float("inf")},
    ],
)
def test_invalid_retry_options_fail_before_network(request_rest, options):
    with pytest.raises(ValueError):
        request_rest("GET", "/v2/pods", api_key="key", **options)


@pytest.mark.asyncio
async def test_cancellation_during_wait_does_not_hold_reservation(
    quota_server, monkeypatch
):
    quota_server["responses"] = [(429, {"Retry-After": "60"}, {"detail": "quota"})]
    sleeping = asyncio.Event()
    original_sleep = asyncio.sleep

    async def sleep(delay):
        sleeping.set()
        await original_sleep(delay)

    monkeypatch.setattr(rest.asyncio, "sleep", sleep)
    pending = asyncio.create_task(
        rest.run_rest_request_async(
            "POST", "/v2/pods", api_key="key", max_rate_limit_wait=120
        )
    )
    try:
        await asyncio.wait_for(sleeping.wait(), 2)
        bucket = next(iter(rest.rate_limits.buckets.values()))
        assert bucket.in_flight == 0
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 1)
        assert bucket.users == 0
        assert bucket.in_flight == 0
        assert bucket.blocked_until > _rate_limit.time.monotonic()
        assert len(quota_server["requests"]) == 1
        assert await rest.run_rest_request_async(
            "GET", "/v2/pods", api_key="other"
        ) == {"id": "pod-1"}
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_shared_wait_is_nonblocking_and_refreshes_quota(quota_server):
    quota_server["responses"] = [
        (200, {"RateLimit": '"minute";r=0;t=1'}, {"id": "pod-1"})
    ]
    await rest.run_rest_request_async("GET", "/v2/pods", api_key="key")
    pending = [
        asyncio.create_task(
            rest.run_rest_request_async("GET", "/v2/templates", api_key="key")
        )
        for _ in range(3)
    ]
    try:
        await asyncio.sleep(0.02)
        assert len(quota_server["requests"]) == 1
        assert not any(task.done() for task in pending)
        assert await rest.run_rest_request_async(
            "GET", "/v2/pods", api_key="other"
        ) == {"id": "pod-1"}
        quota_server["responses"] = [
            (200, {"RateLimit": '"minute";r=10;t=60'}, {"id": "pod-1"})
        ]
        assert (
            await asyncio.wait_for(asyncio.gather(*pending), 3) == [{"id": "pod-1"}] * 3
        )
        assert len(quota_server["requests"]) == 5
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
