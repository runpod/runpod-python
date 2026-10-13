"""Shared REST quotas for threads and asynchronous callers."""

import hashlib
import math
import random
import re
import threading
import time
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from typing import Mapping, Optional
from urllib.parse import urlsplit


def _retry_after(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
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


class _Bucket:
    def __init__(self):
        self.lock = threading.Lock()
        self.windows: dict[str, tuple[Optional[int], float]] = {}
        self.blocked_until = 0.0
        self.discovered = False
        self.in_flight = 0
        self.users = 0
        self.last_used = time.monotonic()

    def acquire(self) -> float:
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.blocked_until - now)
            if self.in_flight and (
                not self.discovered or (self.blocked_until and not delay)
            ):
                delay = 0.05
            for name, (remaining, reset_at) in self.windows.items():
                if now >= reset_at:
                    self.windows[name] = (None, reset_at)
                    if self.in_flight:
                        delay = max(delay, 0.05)
                elif remaining == 0:
                    delay = max(delay, reset_at - now)
            if delay:
                return delay
            self.in_flight += 1
            for name, (remaining, reset_at) in self.windows.items():
                if remaining is not None:
                    self.windows[name] = (max(0, remaining - 1), reset_at)
            return 0.0

    def update(
        self, status: int, headers: Mapping[str, str], attempt: int
    ) -> Optional[float]:
        headers = {key.lower(): value for key, value in headers.items()}
        windows = _windows(headers.get("ratelimit", ""))
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
                remaining = max(0, remaining - max(0, self.in_flight - 1))
                previous, previous_reset = self.windows.get(name, (None, 0.0))
                if previous is not None and now < previous_reset:
                    remaining = min(remaining, previous)
                    reset_at = max(reset_at, previous_reset)
                self.windows[name] = (remaining, reset_at)
            for name in list(self.windows):
                if name not in windows and self.windows[name][1] <= now:
                    del self.windows[name]
            if retry_after is not None:
                retry_after = max(
                    retry_after,
                    *(
                        reset - now
                        for remaining, reset in self.windows.values()
                        if remaining == 0
                    ),
                    0.0,
                )
                self.blocked_until = max(self.blocked_until, now + retry_after)
            return retry_after

    def release(self):
        with self.lock:
            self.in_flight -= 1


class _RateLimits:
    def __init__(self):
        self.lock = threading.Lock()
        self.buckets: dict[tuple[str, str, bytes], _Bucket] = {}

    @contextmanager
    def bucket(self, url: str, api_key: str):
        origin = urlsplit(url)
        key = (
            origin.scheme.lower(),
            origin.netloc.lower(),
            hashlib.sha256(api_key.encode()).digest(),
        )
        with self.lock:
            now = time.monotonic()
            for cached_key, bucket in list(self.buckets.items()):
                if (
                    bucket.users == 0
                    and now - bucket.last_used >= 300
                    and bucket.blocked_until <= now
                    and all(reset <= now for _, reset in bucket.windows.values())
                ):
                    del self.buckets[cached_key]
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
    if not math.isfinite(max_rate_limit_wait) or max_rate_limit_wait < 0:
        raise ValueError("max_rate_limit_wait must be finite and non-negative")


rate_limits = _RateLimits()
