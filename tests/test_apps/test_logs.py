"""unit tests for pod log access."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from runpod.apps.logs import pod_logs, stream_pod_logs, tail_summary


class TestTailSummary:
    def test_empty(self):
        assert tail_summary({}) == "(no logs available)"
        assert tail_summary({"container": [], "system": []}) == (
            "(no logs available)"
        )

    def test_container_only(self):
        out = tail_summary({"container": ["a", "b"], "system": []})
        assert "--- container (last 2) ---" in out
        assert "a" in out and "b" in out
        assert "system" not in out

    def test_tail_limit(self):
        entries = [f"line{i}" for i in range(50)]
        out = tail_summary({"container": entries}, lines=3)
        assert "line49" in out
        assert "line46" not in out
        assert "(last 3)" in out

    def test_system_before_container(self):
        out = tail_summary({"container": ["c"], "system": ["s"]})
        assert out.index("system") < out.index("container")


def _mock_session(response):
    """a ClientSession whose get() context manager yields `response`."""
    get_cm = MagicMock()
    get_cm.__aenter__ = AsyncMock(return_value=response)
    get_cm.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=get_cm)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    return session_cm, session


class TestPodLogs:
    async def test_snapshot_normalizes_nulls(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = AsyncMock(
            return_value={"container": ["hello"], "system": None}
        )
        session_cm, session = _mock_session(response)

        with patch("aiohttp.ClientSession", return_value=session_cm):
            result = await pod_logs("pod123")

        assert result == {"container": ["hello"], "system": []}
        url = session.get.call_args[0][0]
        assert "pod123" in url
        headers = session.get.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer test-key"

    async def test_log_type_param(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = AsyncMock(return_value={})
        session_cm, session = _mock_session(response)

        with patch("aiohttp.ClientSession", return_value=session_cm):
            await pod_logs("pod123", log_type="system")

        assert session.get.call_args[1]["params"] == {"type": "system"}


class _FakeContent:
    def __init__(self, lines):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class TestStreamPodLogs:
    async def test_parses_sse_frames(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
        frames = [
            b": heartbeat\n",
            b"data: " + json.dumps(
                {"source": "container", "line": "hi", "ts": "t1"}
            ).encode() + b"\n",
            b"garbage\n",
            b"data: not-json\n",
            b"data: " + json.dumps(
                {"source": "system", "line": "boot", "ts": "t2"}
            ).encode() + b"\n",
        ]
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.content = _FakeContent(frames)
        session_cm, session = _mock_session(response)

        with patch("aiohttp.ClientSession", return_value=session_cm):
            events = [e async for e in stream_pod_logs("pod123")]

        assert events == [
            {"source": "container", "line": "hi", "ts": "t1"},
            {"source": "system", "line": "boot", "ts": "t2"},
        ]
        params = session.get.call_args[1]["params"]
        assert params["stream"] == "true"
        assert params["tail"] == "100"

    async def test_since_param(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.content = _FakeContent([])
        session_cm, session = _mock_session(response)

        with patch("aiohttp.ClientSession", return_value=session_cm):
            async for _ in stream_pod_logs(
                "pod123", since="2026-01-01T00:00:00Z", tail=5
            ):
                pass

        params = session.get.call_args[1]["params"]
        assert params["since"] == "2026-01-01T00:00:00Z"
        assert params["tail"] == "5"
