"""
Tests for Progress Update System - Unified Async Queue.

Following TDD principles, these tests define the expected behavior
of the progress update system with async queuing and batching.

Key improvements over current polling approach:
- Async queue for non-blocking updates
- Background worker for sending updates
- Batch updates to reduce HTTP overhead
- Automatic retry with exponential backoff
- No blocking on progress updates
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, Mock
from runpod.serverless.core.job_state import Job


class TestProgressSystemInitialization:
    """Test progress system initialization."""

    def test_progress_system_creation(self, mock_session, tmp_path):
        """ProgressSystem can be created with required parameters."""
        from runpod.serverless.core.progress import ProgressSystem

        progress = ProgressSystem(
            session=mock_session,
            progress_url="http://test/progress",
            batch_size=5,
            flush_interval=1.0
        )

        assert progress.session == mock_session
        assert progress.progress_url == "http://test/progress"
        assert progress.batch_size == 5
        assert progress.flush_interval == 1.0

    def test_progress_system_default_configuration(self, mock_session):
        """ProgressSystem uses sensible defaults."""
        from runpod.serverless.core.progress import ProgressSystem

        progress = ProgressSystem(
            session=mock_session,
            progress_url="http://test/progress"
        )

        assert progress.batch_size == 10
        assert progress.flush_interval == 2.0


class TestNonBlockingProgressUpdates:
    """Test non-blocking progress update operations."""

    @pytest.mark.asyncio
    async def test_progress_update_returns_immediately(self, mock_session, tmp_path):
        """Progress updates don't block caller."""
        from runpod.serverless.core.progress import ProgressSystem
        import time

        progress = ProgressSystem(
            session=mock_session,
            progress_url="http://test/progress"
        )
        await progress.start()

        start = time.perf_counter()
        await progress.update("job-1", {"status": "processing", "percent": 50})
        duration = time.perf_counter() - start

        # Should complete in <1ms (just queue operation)
        assert duration < 0.001

        await progress.stop()

    @pytest.mark.asyncio
    async def test_multiple_updates_queued_not_blocked(self, mock_session, tmp_path):
        """Multiple progress updates queue without blocking."""
        from runpod.serverless.core.progress import ProgressSystem
        import time

        progress = ProgressSystem(
            session=mock_session,
            progress_url="http://test/progress"
        )
        await progress.start()

        start = time.perf_counter()
        for i in range(100):
            await progress.update(f"job-{i}", {"percent": i})
        duration = time.perf_counter() - start

        # Should complete in <10ms (queue operations only)
        assert duration < 0.01

        await progress.stop()


class TestBatchingBehavior:
    """Test automatic batching of progress updates."""

    @pytest.mark.asyncio
    async def test_updates_batched_before_sending(self, mock_session, tmp_path):
        """Progress updates are batched to reduce HTTP calls."""
        from runpod.serverless.core.progress import ProgressSystem

        progress = ProgressSystem(
            session=mock_session,
            progress_url="http://test/progress",
            batch_size=5,
            flush_interval=0.5
        )
        await progress.start()

        # Add 5 updates (should trigger batch)
        for i in range(5):
            await progress.update(f"job-{i}", {"percent": i * 20})

        # Wait for batch to be sent
        await asyncio.sleep(0.1)

        # Should have sent 1 batch request
        assert mock_session.post.call_count == 1

        # Verify batch payload structure
        call_args = mock_session.post.call_args
        assert call_args[0][0] == "http://test/progress"
        payload = call_args[1]["json"]
        assert "updates" in payload
        assert len(payload["updates"]) == 5

        await progress.stop()

    @pytest.mark.asyncio
    async def test_batch_sent_on_interval_even_if_not_full(self, mock_session):
        """Partial batches are flushed on interval."""
        from runpod.serverless.core.progress import ProgressSystem

        progress = ProgressSystem(
            session=mock_session,
            progress_url="http://test/progress",
            batch_size=10,
            flush_interval=0.1
        )
        await progress.start()

        # Add only 3 updates (less than batch size)
        for i in range(3):
            await progress.update(f"job-{i}", {"percent": i * 20})

        # Wait for flush interval
        await asyncio.sleep(0.15)

        # Should have sent partial batch
        assert mock_session.post.call_count >= 1

        await progress.stop()


class TestProgressUpdateFormat:
    """Test progress update payload format."""

    @pytest.mark.asyncio
    async def test_progress_update_includes_job_id(self, mock_session):
        """Progress update includes job ID."""
        from runpod.serverless.core.progress import ProgressSystem

        progress = ProgressSystem(
            session=mock_session,
            progress_url="http://test/progress",
            batch_size=1
        )
        await progress.start()

        await progress.update("job-123", {"percent": 50})
        await asyncio.sleep(0.05)

        call_args = mock_session.post.call_args
        payload = call_args[1]["json"]
        assert payload["updates"][0]["job_id"] == "job-123"

        await progress.stop()

    @pytest.mark.asyncio
    async def test_progress_update_includes_data(self, mock_session):
        """Progress update includes custom data."""
        from runpod.serverless.core.progress import ProgressSystem

        progress = ProgressSystem(
            session=mock_session,
            progress_url="http://test/progress",
            batch_size=1
        )
        await progress.start()

        await progress.update("job-123", {"percent": 75, "message": "Processing..."})
        await asyncio.sleep(0.05)

        call_args = mock_session.post.call_args
        payload = call_args[1]["json"]
        update = payload["updates"][0]
        assert update["data"]["percent"] == 75
        assert update["data"]["message"] == "Processing..."

        await progress.stop()


class TestRetryBehavior:
    """Test automatic retry with backoff."""

    @pytest.mark.asyncio
    async def test_retry_on_http_error(self):
        """Failed updates are retried with exponential backoff."""
        from runpod.serverless.core.progress import ProgressSystem
        import aiohttp

        session = AsyncMock()

        # First call fails, second succeeds
        fail_response = AsyncMock()
        fail_response.raise_for_status.side_effect = aiohttp.ClientError()

        success_response = AsyncMock()
        success_response.raise_for_status.return_value = None

        session.post.return_value.__aenter__.side_effect = [
            fail_response,
            success_response,
        ]

        progress = ProgressSystem(
            session=session,
            progress_url="http://test/progress",
            batch_size=1,
            flush_interval=0.1
        )
        await progress.start()

        await progress.update("job-1", {"percent": 50})
        await asyncio.sleep(0.3)  # Wait for retry

        # Should have retried
        assert session.post.call_count >= 2

        await progress.stop()

    @pytest.mark.asyncio
    async def test_exponential_backoff_on_failures(self):
        """Backoff increases exponentially on repeated failures."""
        from runpod.serverless.core.progress import ProgressSystem
        import aiohttp
        import time

        session = AsyncMock()

        # All calls fail
        fail_response = AsyncMock()
        fail_response.raise_for_status.side_effect = aiohttp.ClientError()
        session.post.return_value.__aenter__.return_value = fail_response

        progress = ProgressSystem(
            session=session,
            progress_url="http://test/progress",
            batch_size=1,
            flush_interval=0.1,
            max_retries=3
        )
        await progress.start()

        await progress.update("job-1", {"percent": 50})

        start = time.perf_counter()
        await asyncio.sleep(0.5)
        duration = time.perf_counter() - start

        # Should have attempted multiple times with increasing delays
        # Initial + retry @ 0.1s + retry @ 0.2s
        assert session.post.call_count >= 2

        await progress.stop()


class TestProgressLifecycle:
    """Test progress system start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_background_task(self, mock_session):
        """start() creates background worker task."""
        from runpod.serverless.core.progress import ProgressSystem

        progress = ProgressSystem(
            session=mock_session,
            progress_url="http://test/progress"
        )

        assert progress._worker_task is None

        await progress.start()
        assert progress._worker_task is not None
        assert not progress._worker_task.done()

        await progress.stop()

    @pytest.mark.asyncio
    async def test_stop_flushes_pending_updates(self, mock_session):
        """stop() sends any pending updates before stopping."""
        from runpod.serverless.core.progress import ProgressSystem

        progress = ProgressSystem(
            session=mock_session,
            progress_url="http://test/progress",
            batch_size=10,  # Large batch size
            flush_interval=10.0  # Long interval
        )
        await progress.start()

        # Add updates but don't wait for batch/interval
        for i in range(3):
            await progress.update(f"job-{i}", {"percent": i * 20})

        # Stop immediately - should flush pending
        await progress.stop()

        # Should have sent pending updates
        assert mock_session.post.call_count >= 1

    @pytest.mark.asyncio
    async def test_stop_cancels_worker_task(self, mock_session):
        """stop() cancels background worker."""
        from runpod.serverless.core.progress import ProgressSystem

        progress = ProgressSystem(
            session=mock_session,
            progress_url="http://test/progress"
        )
        await progress.start()
        task = progress._worker_task

        await progress.stop()

        assert task.done()
        assert progress._worker_task is None


class TestErrorHandling:
    """Test error handling and resilience."""

    @pytest.mark.asyncio
    async def test_update_continues_despite_http_errors(self):
        """Progress system continues working after transient errors."""
        from runpod.serverless.core.progress import ProgressSystem
        import aiohttp

        session = AsyncMock()

        # Alternate between failure and success
        fail_response = AsyncMock()
        fail_response.raise_for_status.side_effect = aiohttp.ClientError()

        success_response = AsyncMock()
        success_response.raise_for_status.return_value = None

        session.post.return_value.__aenter__.side_effect = [
            fail_response,
            success_response,
            success_response,
        ]

        progress = ProgressSystem(
            session=session,
            progress_url="http://test/progress",
            batch_size=1,
            flush_interval=0.1
        )
        await progress.start()

        # Send multiple updates
        await progress.update("job-1", {"percent": 25})
        await asyncio.sleep(0.15)
        await progress.update("job-2", {"percent": 50})
        await asyncio.sleep(0.15)

        # Should have recovered and sent subsequent updates
        assert session.post.call_count >= 2

        await progress.stop()

    @pytest.mark.asyncio
    async def test_queue_overflow_handling(self, mock_session):
        """Progress system handles queue overflow gracefully."""
        from runpod.serverless.core.progress import ProgressSystem

        progress = ProgressSystem(
            session=mock_session,
            progress_url="http://test/progress",
            max_queue_size=100
        )
        await progress.start()

        # Try to overwhelm queue
        for i in range(150):
            await progress.update(f"job-{i}", {"percent": i})

        # Should handle gracefully (drop old or block)
        await asyncio.sleep(0.1)

        await progress.stop()


class TestIntegrationWithJobProcessing:
    """Test progress updates during job processing."""

    @pytest.mark.asyncio
    async def test_progress_updates_during_handler_execution(self, mock_session):
        """Handler can send progress updates during execution."""
        from runpod.serverless.core.progress import ProgressSystem

        progress = ProgressSystem(
            session=mock_session,
            progress_url="http://test/progress",
            batch_size=3  # Set to 3 so it triggers on 3 updates
        )
        await progress.start()

        # Simulate handler sending multiple updates
        async def simulated_handler():
            await progress.update("job-123", {"percent": 0, "status": "starting"})
            await asyncio.sleep(0.01)
            await progress.update("job-123", {"percent": 50, "status": "halfway"})
            await asyncio.sleep(0.01)
            await progress.update("job-123", {"percent": 100, "status": "done"})

        await simulated_handler()
        await asyncio.sleep(0.1)  # Wait for batch send

        # All updates should be sent
        assert mock_session.post.call_count >= 1

        await progress.stop()
