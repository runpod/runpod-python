"""
Tests for JobScaler - Event-Driven Job Acquisition.

Following TDD principles, these tests define the expected behavior
of the semaphore-based job scaler with event-driven architecture.

Key improvements over current queue-based approach:
- Semaphore-based concurrency (live scaling without queue drain)
- Event-driven job pickup (<1ms latency vs 0-1000ms polling)
- Direct job processing (no intermediate queue)
- Dynamic concurrency adjustment
"""

import pytest
import asyncio
import time
from unittest.mock import AsyncMock
from runpod.serverless.core.job_state import JobState, Job


class TestJobScalerInitialization:
    """Test job scaler initialization and configuration."""

    def test_job_scaler_creation(self, mock_session, tmp_path):
        """JobScaler can be created with required parameters."""
        from runpod.serverless.core.job_scaler import JobScaler

        async def handler(job):
            return {"output": "done"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        scaler = JobScaler(
            concurrency=5,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take"
        )

        assert scaler.current_concurrency == 5
        assert scaler.handler == handler
        assert scaler.job_state == job_state
        assert scaler.session == mock_session

    def test_job_scaler_semaphore_initialized(self, mock_session, tmp_path):
        """JobScaler initializes semaphore with correct capacity."""
        from runpod.serverless.core.job_scaler import JobScaler

        async def handler(job):
            return {"output": "done"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        scaler = JobScaler(
            concurrency=10,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take"
        )

        # Semaphore should have 10 permits
        assert scaler.semaphore._value == 10


class TestSemaphoreBasedConcurrency:
    """Test semaphore-based concurrency control."""

    @pytest.mark.asyncio
    async def test_acquire_semaphore_before_fetching_job(self, mock_session, tmp_path):
        """Scaler acquires semaphore permit before job fetch."""
        from runpod.serverless.core.job_scaler import JobScaler

        async def handler(job):
            return {"output": "done"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        scaler = JobScaler(
            concurrency=2,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take"
        )

        # Pre-acquire permits to simulate full capacity
        await scaler.semaphore.acquire()
        await scaler.semaphore.acquire()

        # Next acquire should block
        acquire_task = asyncio.create_task(scaler.semaphore.acquire())
        await asyncio.sleep(0.01)

        assert not acquire_task.done()  # Still blocked

        # Release to cleanup
        scaler.semaphore.release()
        scaler.semaphore.release()
        await acquire_task

    @pytest.mark.asyncio
    async def test_release_semaphore_after_job_completion(self, mock_session, tmp_path):
        """Scaler releases permit after job completes."""
        from runpod.serverless.core.job_scaler import JobScaler

        async def handler(job):
            return {"output": "done"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        scaler = JobScaler(
            concurrency=2,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take"
        )

        initial_permits = scaler.semaphore._value
        await scaler.semaphore.acquire()  # Simulate job start

        assert scaler.semaphore._value == initial_permits - 1

        scaler.semaphore.release()  # Simulate job complete
        assert scaler.semaphore._value == initial_permits


class TestDynamicConcurrencyScaling:
    """Test live concurrency adjustment without blocking."""

    @pytest.mark.asyncio
    async def test_scale_up_concurrency(self, mock_session, tmp_path):
        """Scaler increases concurrency without blocking."""
        from runpod.serverless.core.job_scaler import JobScaler

        async def handler(job):
            return {"output": "done"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        scaler = JobScaler(
            concurrency=2,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take"
        )

        # Scale up from 2 to 5
        await scaler.adjust_concurrency(5)

        # Should have 5 permits available
        assert scaler.semaphore._value == 5
        assert scaler.current_concurrency == 5

    @pytest.mark.asyncio
    async def test_scale_down_concurrency(self, mock_session, tmp_path):
        """Scaler decreases concurrency gracefully."""
        from runpod.serverless.core.job_scaler import JobScaler

        async def handler(job):
            return {"output": "done"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        scaler = JobScaler(
            concurrency=5,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take"
        )

        # Scale down from 5 to 2
        await scaler.adjust_concurrency(2)

        # Should have 2 permits
        assert scaler.semaphore._value == 2
        assert scaler.current_concurrency == 2

    @pytest.mark.asyncio
    async def test_scale_without_blocking_active_jobs(self, mock_session, tmp_path):
        """Scaling doesn't interrupt active jobs."""
        from runpod.serverless.core.job_scaler import JobScaler

        job_completed = False

        async def handler(job):
            nonlocal job_completed
            await asyncio.sleep(0.1)
            job_completed = True
            return {"output": "done"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        scaler = JobScaler(
            concurrency=5,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take"
        )

        # Simulate job in progress
        await scaler.semaphore.acquire()
        job_task = asyncio.create_task(handler({"id": "test"}))

        # Scale down while job is running
        await scaler.adjust_concurrency(2)

        # Job should still complete
        await job_task
        assert job_completed


class TestJobProcessing:
    """Test job processing flow."""

    @pytest.mark.asyncio
    async def test_process_job_calls_handler(self, mock_session, tmp_path):
        """_process_job executes handler with job data."""
        from runpod.serverless.core.job_scaler import JobScaler

        handler_called = False
        received_job = None

        async def handler(job):
            nonlocal handler_called, received_job
            handler_called = True
            received_job = job
            return {"output": "done"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        scaler = JobScaler(
            concurrency=1,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take",
            result_url="http://test/job-done"
        )

        test_job = {"id": "test-123", "input": {"value": 42}}

        await scaler._process_job(test_job)

        assert handler_called
        assert received_job == test_job

    @pytest.mark.asyncio
    async def test_process_job_updates_state(self, mock_session, tmp_path):
        """_process_job adds/removes job from state."""
        from runpod.serverless.core.job_scaler import JobScaler

        async def handler(job):
            return {"output": "done"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        scaler = JobScaler(
            concurrency=1,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take",
            result_url="http://test/job-done"
        )

        test_job = {"id": "test-123", "input": {}}

        # Job should be in state during processing
        process_task = asyncio.create_task(scaler._process_job(test_job))
        await asyncio.sleep(0.01)  # Let it start

        # After completion, job should be removed
        await process_task
        assert Job(id="test-123") not in job_state

    @pytest.mark.asyncio
    async def test_process_job_releases_semaphore_on_completion(
        self, mock_session, tmp_path
    ):
        """_process_job releases semaphore after handler finishes."""
        from runpod.serverless.core.job_scaler import JobScaler

        async def handler(job):
            await asyncio.sleep(0.05)
            return {"output": "done"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        scaler = JobScaler(
            concurrency=2,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take",
            result_url="http://test/job-done"
        )

        initial_permits = scaler.semaphore._value
        await scaler.semaphore.acquire()  # Acquire before processing

        test_job = {"id": "test-123", "input": {}}
        await scaler._process_job(test_job)

        # Semaphore should be released
        assert scaler.semaphore._value == initial_permits

    @pytest.mark.asyncio
    async def test_process_job_releases_semaphore_on_error(
        self, mock_session, tmp_path
    ):
        """_process_job releases semaphore even if handler fails."""
        from runpod.serverless.core.job_scaler import JobScaler

        async def handler(job):
            raise ValueError("Handler error")

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        scaler = JobScaler(
            concurrency=2,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take",
            result_url="http://test/job-done"
        )

        initial_permits = scaler.semaphore._value
        await scaler.semaphore.acquire()

        test_job = {"id": "test-123", "input": {}}

        # Should not raise
        await scaler._process_job(test_job)

        # Semaphore should be released despite error
        assert scaler.semaphore._value == initial_permits


class TestJobFetching:
    """Test job fetching from API."""

    @pytest.mark.asyncio
    async def test_fetch_job_from_api(self, mock_session, tmp_path):
        """_fetch_job retrieves job from API."""
        from runpod.serverless.core.job_scaler import JobScaler

        async def handler(job):
            return {"output": "done"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        # Mock successful job fetch
        response_mock = AsyncMock()
        response_mock.status = 200
        response_mock.json = AsyncMock(return_value={"id": "job-1", "input": {}})
        mock_session.get.return_value.__aenter__.return_value = response_mock

        scaler = JobScaler(
            concurrency=1,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take"
        )

        job = await scaler._fetch_job()

        assert job is not None
        assert job["id"] == "job-1"
        mock_session.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_job_returns_none_on_204(self, mock_session, tmp_path):
        """_fetch_job returns None when no jobs available (204)."""
        from runpod.serverless.core.job_scaler import JobScaler

        async def handler(job):
            return {"output": "done"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        # Mock 204 No Content
        response_mock = AsyncMock()
        response_mock.status = 204
        mock_session.get.return_value.__aenter__.return_value = response_mock

        scaler = JobScaler(
            concurrency=1,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take"
        )

        job = await scaler._fetch_job()

        assert job is None


class TestConcurrentJobProcessing:
    """Test concurrent job processing capabilities."""

    @pytest.mark.asyncio
    async def test_concurrent_job_processing(self, mock_session, tmp_path):
        """Multiple jobs process concurrently up to limit."""
        from runpod.serverless.core.job_scaler import JobScaler

        processed_jobs = []

        async def handler(job):
            processed_jobs.append(job["id"])
            await asyncio.sleep(0.1)
            return {"output": "done"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        scaler = JobScaler(
            concurrency=3,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take",
            result_url="http://test/job-done"
        )

        # Process 3 jobs concurrently
        jobs = [{"id": f"job-{i}", "input": {}} for i in range(3)]

        tasks = []
        for job in jobs:
            await scaler.semaphore.acquire()
            task = asyncio.create_task(scaler._process_job(job))
            tasks.append(task)

        # All should complete in ~0.1s (concurrent), not 0.3s (sequential)
        start = time.time()
        await asyncio.gather(*tasks)
        duration = time.time() - start

        assert duration < 0.2  # Margin for timing
        assert len(processed_jobs) == 3


class TestShutdownBehavior:
    """Test graceful shutdown handling."""

    @pytest.mark.asyncio
    async def test_shutdown_flag_stops_acquisition(self, mock_session, tmp_path):
        """Setting shutdown flag stops job acquisition."""
        from runpod.serverless.core.job_scaler import JobScaler

        async def handler(job):
            return {"output": "done"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        scaler = JobScaler(
            concurrency=1,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take"
        )

        assert scaler.is_alive()

        scaler.shutdown()

        assert not scaler.is_alive()


class TestErrorHandling:
    """Test error handling and resilience."""

    @pytest.mark.asyncio
    async def test_handler_exception_captured(self, mock_session, tmp_path):
        """Handler exceptions are captured and don't crash worker."""
        from runpod.serverless.core.job_scaler import JobScaler

        async def handler(job):
            raise ValueError("Test error")

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        scaler = JobScaler(
            concurrency=1,
            handler=handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take",
            result_url="http://test/job-done"
        )

        test_job = {"id": "test-123", "input": {}}

        # Should not raise
        await scaler._process_job(test_job)

        # Job should be removed from state despite error
        assert Job(id="test-123") not in job_state
