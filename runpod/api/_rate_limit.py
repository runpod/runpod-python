"""Shared REST quotas for threads and asynchronous callers."""

import hmac
import math
import os
import random
import re
import threading
import time
from contextlib import contextmanager
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Mapping, Optional
from urllib.parse import urlsplit

from runpod.error import RateLimitError


def _retry_after(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            delay = date.timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return None
        delay = max(0.0, delay)
    return delay if math.isfinite(delay) and delay >= 0 else None


def _windows(value: str) -> dict[str, tuple[int, float]]:
    windows = {}
    for member in value.split(","):
        parts = member.strip().split(";")
        if parts[0] not in {'"minute"', '"hour"', '"day"'}:
            continue
        parameters = {}
        for part in parts[1:]:
            match = re.fullmatch(r"\s*([a-z]+)\s*=\s*([0-9]{1,15})\s*", part)
            if match:
                parameters[match[1]] = int(match[2])
        if "r" in parameters and "t" in parameters:
            windows[parts[0]] = (parameters["r"], float(parameters["t"]))
    return windows


class _RetryBudget:
    def __init__(self, max_retries: int, max_wait: float, query: str):
        self.max_retries = max_retries
        self.remaining_wait = max_wait
        self.query = query

    def wait(self, delay: float) -> None:
        if delay > self.remaining_wait:
            raise RateLimitError(
                "Rate limit wait budget exceeded", self.query, retry_after=delay
            )
        self.remaining_wait -= delay

    def can_retry(self, attempt: int, retry_after: Optional[float]) -> bool:
        return (
            retry_after is not None
            and attempt < self.max_retries
            and retry_after <= self.remaining_wait
        )


class _Bucket:
    def __init__(self):
        self.lock = threading.Lock()
        self.windows: dict[str, tuple[Optional[int], float]] = {}
        self.estimates: dict[str, int] = {}
        self.reset_lower_bounds: dict[str, float] = {}
        self.blocked_until = 0.0
        self.discovered = False
        self.in_flight = 0
        self.users = 0
        self.last_used = time.monotonic()

    def acquire(self, budget: Optional[_RetryBudget] = None) -> float:
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.blocked_until - now)
            probing = self.in_flight and (
                not self.discovered or (self.blocked_until and not delay)
            )
            for name, (remaining, reset_at) in self.windows.items():
                if now >= reset_at:
                    self.windows[name] = (None, reset_at)
                    if self.in_flight:
                        probing = True
                elif remaining == 0:
                    delay = max(delay, reset_at - now)
            if delay:
                if budget is not None:
                    budget.wait(delay)
                return delay
            if probing:
                return 0.05
            self.in_flight += 1
            for name, (remaining, reset_at) in self.windows.items():
                if remaining is not None:
                    self.windows[name] = (max(0, remaining - 1), reset_at)
                    self.estimates[name] = max(0, self.estimates[name] - 1)
            return 0.0

    def update(
        self,
        status: int,
        headers: Mapping[str, str],
        attempt: int,
        *,
        started_at: Optional[float] = None,
    ) -> Optional[float]:
        windows = _windows(
            ", ".join(
                value for key, value in headers.items() if key.lower() == "ratelimit"
            )
        )
        headers = {key.lower(): value for key, value in headers.items()}
        retry_after = None
        if status == 429:
            retry_after = _retry_after(headers.get("retry-after"))
            if retry_after is None:
                retry_after = random.uniform(0.5, 1.0) * 2 ** min(attempt, 6)
        with self.lock:
            now = time.monotonic()
            self.discovered = True
            if self.blocked_until <= now:
                self.blocked_until = 0.0
            for name, (remaining, reset_after) in windows.items():
                reset_at = now + reset_after
                reset_lower = (now if started_at is None else started_at) + max(
                    0.0, reset_after - 1.0
                )
                previous, previous_reset = self.windows.get(name, (None, 0.0))
                previous_lower = self.reset_lower_bounds.get(name, 0.0)
                if reset_at < previous_lower:
                    continue
                if (
                    previous is not None
                    and now < previous_reset
                    and reset_lower <= previous_reset
                ):
                    remaining = min(remaining, self.estimates[name])
                    reset_at = min(reset_at, previous_reset)
                    reset_lower = max(reset_lower, previous_lower)
                self.estimates[name] = remaining
                self.reset_lower_bounds[name] = reset_lower
                self.windows[name] = (
                    max(0, remaining - max(0, self.in_flight - 1)),
                    reset_at,
                )
            for name in list(self.windows):
                if name not in windows and self.windows[name][1] <= now:
                    del self.windows[name]
                    del self.estimates[name]
                    del self.reset_lower_bounds[name]
            if retry_after is not None:
                retry_after = max(
                    retry_after,
                    self.blocked_until - now,
                    *(
                        reset - now
                        for name, (_, reset) in self.windows.items()
                        if self.estimates[name] == 0
                    ),
                    0.0,
                )
                self.blocked_until = max(self.blocked_until, now + retry_after)
            return retry_after

    def release(self):
        with self.lock:
            self.in_flight -= 1
            for name, (remaining, reset_at) in self.windows.items():
                if remaining is not None:
                    self.windows[name] = (
                        max(0, self.estimates[name] - self.in_flight),
                        reset_at,
                    )


class _RateLimits:
    def __init__(self):
        self.lock = threading.Lock()
        self.fingerprint_key = os.urandom(32)
        self.buckets: dict[tuple[str, str, bytes], _Bucket] = {}
        self.next_cleanup = 0.0

    @contextmanager
    def bucket(self, url: str, api_key: str):
        origin = urlsplit(url)
        key = (
            origin.scheme.lower(),
            origin.netloc.lower(),
            hmac.digest(self.fingerprint_key, api_key.encode(), "sha256"),
        )
        with self.lock:
            now = time.monotonic()
            if now >= self.next_cleanup:
                for cached_key, bucket in list(self.buckets.items()):
                    if bucket.users or now - bucket.last_used < 300:
                        continue
                    with bucket.lock:
                        if bucket.blocked_until <= now and all(
                            reset <= now for _, reset in bucket.windows.values()
                        ):
                            del self.buckets[cached_key]
                self.next_cleanup = now + 60
            bucket = self.buckets.setdefault(key, _Bucket())
            bucket.users += 1
        try:
            yield bucket
        finally:
            with self.lock:
                bucket.users -= 1
                bucket.last_used = time.monotonic()


def _validate_retry_options(max_retries: int, max_rate_limit_wait: float) -> None:
    if (
        isinstance(max_retries, bool)
        or not isinstance(max_retries, int)
        or max_retries < 0
    ):
        raise ValueError("max_retries must be a non-negative integer")
    if (
        isinstance(max_rate_limit_wait, bool)
        or not isinstance(max_rate_limit_wait, (int, float))
        or not math.isfinite(max_rate_limit_wait)
        or max_rate_limit_wait < 0
    ):
        raise ValueError("max_rate_limit_wait must be finite and non-negative")


rate_limits = _RateLimits()
