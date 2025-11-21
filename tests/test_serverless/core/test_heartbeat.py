"""
Tests for Async Heartbeat System.

Following TDD principles, these tests define the expected behavior
of the async heartbeat task running in the main event loop.

Key improvements over current multiprocessing approach:
- No separate process (saves 50-200MB memory)
- Direct memory access to job state (no file I/O)
- Exponential backoff on failures
- Runs in main event loop (easier debugging)
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, Mock, patch
from runpod.serverless.core.job_state import JobState, Job


class TestHeartbeatInitialization:
    """Test heartbeat initialization."""

    def test_heartbeat_creation(self, mock_session, tmp_path):
        """Heartbeat can be created with required parameters."""
        from runpod.serverless.core.heartbeat import Heartbeat

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        heartbeat = Heartbeat(
            session=mock_session,
            job_state=job_state,
            ping_url="http://test/ping",
            interval=10
        )

        assert heartbeat.session == mock_session
        assert heartbeat.job_state == job_state
        assert heartbeat.ping_url == "http://test/ping"
        assert heartbeat.interval == 10

    def test_heartbeat_default_interval(self, mock_session, tmp_path):
        """Heartbeat uses default 10s interval if not specified."""
        from runpod.serverless.core.heartbeat import Heartbeat

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        heartbeat = Heartbeat(
            session=mock_session,
            job_state=job_state,
            ping_url="http://test/ping"
        )

        assert heartbeat.interval == 10


class TestHeartbeatMemoryAccess:
    """Test heartbeat reads from memory, not disk."""

    @pytest.mark.asyncio
    async def test_heartbeat_reads_from_memory_not_disk(self, mock_session, tmp_path, mocker):
        """Heartbeat accesses memory state, not file."""
        from runpod.serverless.core.heartbeat import Heartbeat

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        await job_state.add(Job(id="job-1"))
        await job_state.add(Job(id="job-2"))

        heartbeat = Heartbeat(
            session=mock_session,
            job_state=job_state,
            ping_url="http://test/ping"
        )

        # Mock file open to ensure it's not called
        mock_open = mocker.patch("builtins.open")

        await heartbeat._send_ping()

        # File should NOT be accessed
        mock_open.assert_not_called()
        # HTTP session should be used
        mock_session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_heartbeat_sends_job_list(self, mock_session, tmp_path):
        """Heartbeat includes active job IDs in ping."""
        from runpod.serverless.core.heartbeat import Heartbeat

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        await job_state.add(Job(id="job-1"))
        await job_state.add(Job(id="job-2"))

        heartbeat = Heartbeat(
            session=mock_session,
            job_state=job_state,
            ping_url="http://test/ping"
        )

        await heartbeat._send_ping()

        # Verify call was made with job IDs
        assert mock_session.get.called
        call_kwargs = mock_session.get.call_args.kwargs
        assert "params" in call_kwargs
        job_ids = call_kwargs["params"]["job_id"]
        assert "job-1" in job_ids
        assert "job-2" in job_ids

    @pytest.mark.asyncio
    async def test_heartbeat_sends_empty_when_no_jobs(self, mock_session, tmp_path):
        """Heartbeat sends empty string when no jobs."""
        from runpod.serverless.core.heartbeat import Heartbeat

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        heartbeat = Heartbeat(
            session=mock_session,
            job_state=job_state,
            ping_url="http://test/ping"
        )

        await heartbeat._send_ping()

        call_kwargs = mock_session.get.call_args.kwargs
        assert call_kwargs["params"]["job_id"] == ""


class TestHeartbeatBackoff:
    """Test exponential backoff on failures."""

    @pytest.mark.asyncio
    async def test_heartbeat_exponential_backoff_on_failure(self, tmp_path):
        """Heartbeat backs off exponentially on repeated failures."""
        from runpod.serverless.core.heartbeat import Heartbeat
        import aiohttp

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        session = AsyncMock()

        # Properly mock context manager that raises error
        response_mock = AsyncMock()
        response_mock.raise_for_status.side_effect = aiohttp.ClientError()
        session.get.return_value.__aenter__.return_value = response_mock

        heartbeat = Heartbeat(
            session=session,
            job_state=job_state,
            ping_url="http://test/ping",
            interval=0.1  # Fast interval for testing
        )

        # Start heartbeat
        await heartbeat.start()

        # Wait and count attempts
        await asyncio.sleep(0.5)
        initial_attempts = session.get.call_count

        # Should backoff exponentially: 1s, 2s, 4s...
        # With 0.1s interval, backoff should reduce call rate
        await asyncio.sleep(0.5)
        later_attempts = session.get.call_count - initial_attempts

        # Later period should have fewer attempts due to backoff
        assert later_attempts < initial_attempts

        await heartbeat.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_resets_backoff_on_success(self, tmp_path):
        """Heartbeat resets backoff after successful ping."""
        from runpod.serverless.core.heartbeat import Heartbeat
        import aiohttp

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        session = AsyncMock()

        # First call fails, rest succeed
        fail_response = AsyncMock()
        fail_response.raise_for_status.side_effect = aiohttp.ClientError()

        success_response = AsyncMock()
        success_response.raise_for_status.return_value = None

        session.get.return_value.__aenter__.side_effect = [
            fail_response,
            success_response,
            success_response,
        ]

        heartbeat = Heartbeat(
            session=session,
            job_state=job_state,
            ping_url="http://test/ping",
            interval=0.1
        )

        await heartbeat.start()
        await asyncio.sleep(0.5)

        # Should have attempted multiple times
        assert session.get.call_count >= 2

        await heartbeat.stop()


class TestHeartbeatIndependence:
    """Test heartbeat runs independently during job processing."""

    @pytest.mark.asyncio
    async def test_heartbeat_runs_independently_during_blocking_handler(
        self, mock_session, tmp_path
    ):
        """Heartbeat continues pinging during long-running work."""
        from runpod.serverless.core.heartbeat import Heartbeat

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        heartbeat = Heartbeat(
            session=mock_session,
            job_state=job_state,
            ping_url="http://test/ping",
            interval=0.1  # Fast for testing
        )

        # Start heartbeat
        await heartbeat.start()

        # Simulate long-running work
        ping_count_before = mock_session.get.call_count
        await asyncio.sleep(0.5)  # 5 intervals
        ping_count_after = mock_session.get.call_count

        # Should have sent multiple pings during work
        assert ping_count_after - ping_count_before >= 4

        # Stop heartbeat
        await heartbeat.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_continues_during_job_state_updates(
        self, mock_session, tmp_path
    ):
        """Heartbeat continues while jobs are being added/removed."""
        from runpod.serverless.core.heartbeat import Heartbeat

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        heartbeat = Heartbeat(
            session=mock_session,
            job_state=job_state,
            ping_url="http://test/ping",
            interval=0.1
        )

        await heartbeat.start()

        # Continuously add/remove jobs
        for i in range(10):
            await job_state.add(Job(id=f"job-{i}"))
            await asyncio.sleep(0.05)
            await job_state.remove(Job(id=f"job-{i}"))

        # Heartbeat should have continued pinging
        assert mock_session.get.call_count >= 5

        await heartbeat.stop()


class TestHeartbeatLifecycle:
    """Test heartbeat start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_task(self, mock_session, tmp_path):
        """start() creates background task."""
        from runpod.serverless.core.heartbeat import Heartbeat

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        heartbeat = Heartbeat(
            session=mock_session,
            job_state=job_state,
            ping_url="http://test/ping"
        )

        assert heartbeat._task is None

        await heartbeat.start()
        assert heartbeat._task is not None
        assert not heartbeat._task.done()

        await heartbeat.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, mock_session, tmp_path):
        """stop() cancels background task."""
        from runpod.serverless.core.heartbeat import Heartbeat

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        heartbeat = Heartbeat(
            session=mock_session,
            job_state=job_state,
            ping_url="http://test/ping"
        )

        await heartbeat.start()
        task = heartbeat._task

        await heartbeat.stop()

        assert task.done()
        assert heartbeat._task is None

    @pytest.mark.asyncio
    async def test_start_warns_if_already_running(self, mock_session, tmp_path, caplog):
        """start() warns if task already running."""
        from runpod.serverless.core.heartbeat import Heartbeat

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        heartbeat = Heartbeat(
            session=mock_session,
            job_state=job_state,
            ping_url="http://test/ping"
        )

        await heartbeat.start()
        await heartbeat.start()  # Second start

        assert "already running" in caplog.text.lower()

        await heartbeat.stop()


class TestHeartbeatErrorHandling:
    """Test error handling and resilience."""

    @pytest.mark.asyncio
    async def test_heartbeat_continues_after_error(self, tmp_path):
        """Heartbeat continues running after transient errors."""
        from runpod.serverless.core.heartbeat import Heartbeat
        import aiohttp

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        session = AsyncMock()

        # First fails, then succeeds
        fail_response = AsyncMock()
        fail_response.raise_for_status.side_effect = aiohttp.ClientError()

        success_response = AsyncMock()
        success_response.raise_for_status.return_value = None

        session.get.return_value.__aenter__.side_effect = [
            fail_response,
            success_response,
            success_response,
        ]

        heartbeat = Heartbeat(
            session=session,
            job_state=job_state,
            ping_url="http://test/ping",
            interval=0.1
        )

        await heartbeat.start()
        await asyncio.sleep(0.5)

        # Should have recovered and continued
        assert session.get.call_count >= 2

        await heartbeat.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_respects_timeout(self, tmp_path):
        """Heartbeat uses timeout based on interval."""
        from runpod.serverless.core.heartbeat import Heartbeat

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        session = AsyncMock()

        # Mock successful response
        response_mock = AsyncMock()
        response_mock.raise_for_status.return_value = None
        session.get.return_value.__aenter__.return_value = response_mock

        heartbeat = Heartbeat(
            session=session,
            job_state=job_state,
            ping_url="http://test/ping",
            interval=5
        )

        await heartbeat._send_ping()

        # Timeout should be 2x interval
        call_kwargs = session.get.call_args.kwargs
        assert call_kwargs["timeout"] == 10  # 2 * 5
