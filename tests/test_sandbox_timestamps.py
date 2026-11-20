"""RFC3339 timestamps retain microseconds and opaque stream identifiers."""

from datetime import datetime, timedelta, timezone

import pytest

from runpod.sandbox.models import LogEvent, SandboxInfo


@pytest.mark.parametrize("fraction", ["", *["." + "123456789"[:n] for n in range(1, 10)]])
@pytest.mark.parametrize("offset", ["Z", "+00:00", "+05:30", "-07:00"])
def test_log_event_fractional_timestamp_preserves_id(fraction, offset):
    timestamp = f"2026-05-01T22:00:01{fraction}{offset}"
    event_id = f"container/{timestamp}/42"
    zone = {
        "Z": timezone.utc,
        "+00:00": timezone.utc,
        "+05:30": timezone(timedelta(hours=5, minutes=30)),
        "-07:00": timezone(-timedelta(hours=7)),
    }[offset]
    expected = datetime(
        2026, 5, 1, 22, 0, 1, int(fraction[1:7].ljust(6, "0")), tzinfo=zone
    )
    event = LogEvent.from_dict(
        {"source": "container", "line": "hello", "ts": timestamp, "id": event_id}
    )
    assert event.timestamp == expected
    assert event.id == event_id


@pytest.mark.parametrize("fraction, microseconds", [("5", 500000), ("123456789", 123456)])
def test_snapshot_fractional_timestamps(fraction, microseconds):
    timestamp = f"2026-05-01T22:00:01.{fraction}Z"
    fields = {
        "lastActivityAt": "last_activity_at",
        "idleExpiresAt": "idle_expires_at",
        "expiresAt": "expires_at",
        "createdAt": "created_at",
        "updatedAt": "updated_at",
        "terminatedAt": "terminated_at",
        "startedAt": "started_at",
    }
    snapshot = SandboxInfo.from_dict(
        {
            "id": "sandbox-1",
            "name": "sandbox-1",
            "state": "RUNNING",
            "idleTimeoutSeconds": 300,
            "maxLifetimeSeconds": 900,
            **dict.fromkeys(fields, timestamp),
        }
    )
    expected = datetime(2026, 5, 1, 22, 0, 1, microseconds, tzinfo=timezone.utc)
    for attribute in fields.values():
        assert getattr(snapshot, attribute) == expected
