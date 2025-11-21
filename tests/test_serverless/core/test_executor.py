"""
Tests for Automatic Executor Detection - CPU-Blocking Handler Protection.

Following TDD principles, these tests define the expected behavior
of the automatic executor detection system for protecting the event loop
from CPU-blocking handlers.

Key improvements:
- Automatic detection of async vs sync handlers
- Thread pool execution for CPU-blocking sync handlers
- Event loop protection from blocking operations
- Configurable thread pool size
"""

import pytest
import asyncio
import time
from unittest.mock import AsyncMock, Mock


class TestExecutorInitialization:
    """Test executor initialization."""

    def test_executor_creation(self):
        """JobExecutor can be created with configuration."""
        from runpod.serverless.core.executor import JobExecutor

        executor = JobExecutor(max_workers=5)

        assert executor.max_workers == 5

    def test_executor_default_configuration(self):
        """JobExecutor uses sensible defaults."""
        from runpod.serverless.core.executor import JobExecutor

        executor = JobExecutor()

        # Default should use CPU count
        assert executor.max_workers > 0


class TestHandlerTypeDetection:
    """Test automatic handler type detection."""

    def test_detect_async_handler(self):
        """Executor detects async handlers."""
        from runpod.serverless.core.executor import JobExecutor

        async def async_handler(job):
            return {"output": "async"}

        executor = JobExecutor()
        is_async = executor.is_async_handler(async_handler)

        assert is_async is True

    def test_detect_sync_handler(self):
        """Executor detects sync handlers."""
        from runpod.serverless.core.executor import JobExecutor

        def sync_handler(job):
            return {"output": "sync"}

        executor = JobExecutor()
        is_async = executor.is_async_handler(sync_handler)

        assert is_async is False


class TestAsyncHandlerExecution:
    """Test async handler execution."""

    @pytest.mark.asyncio
    async def test_execute_async_handler_directly(self):
        """Async handlers execute directly in event loop."""
        from runpod.serverless.core.executor import JobExecutor

        called_with = None

        async def async_handler(job):
            nonlocal called_with
            called_with = job
            await asyncio.sleep(0.01)
            return {"output": f"processed-{job['id']}"}

        executor = JobExecutor()
        job = {"id": "test-123", "input": {}}

        result = await executor.execute(async_handler, job)

        assert result["output"] == "processed-test-123"
        assert called_with == job

    @pytest.mark.asyncio
    async def test_async_handler_runs_in_event_loop(self):
        """Async handlers run in the main event loop."""
        from runpod.serverless.core.executor import JobExecutor
        import threading

        handler_thread_id = None

        async def async_handler(job):
            nonlocal handler_thread_id
            handler_thread_id = threading.current_thread().ident
            return {"output": "done"}

        executor = JobExecutor()
        job = {"id": "test-123", "input": {}}

        await executor.execute(async_handler, job)

        # Should run in same thread (event loop)
        assert handler_thread_id == threading.current_thread().ident


class TestSyncHandlerExecution:
    """Test sync (potentially CPU-blocking) handler execution."""

    @pytest.mark.asyncio
    async def test_execute_sync_handler_in_thread_pool(self):
        """Sync handlers execute in thread pool."""
        from runpod.serverless.core.executor import JobExecutor
        import threading

        handler_thread_id = None

        def sync_handler(job):
            nonlocal handler_thread_id
            handler_thread_id = threading.current_thread().ident
            time.sleep(0.05)  # Simulate CPU work
            return {"output": f"processed-{job['id']}"}

        executor = JobExecutor()
        job = {"id": "test-123", "input": {}}

        result = await executor.execute(sync_handler, job)

        assert result["output"] == "processed-test-123"
        # Should run in different thread
        assert handler_thread_id != threading.current_thread().ident

    @pytest.mark.asyncio
    async def test_sync_handler_does_not_block_event_loop(self):
        """Sync handlers don't block other async operations."""
        from runpod.serverless.core.executor import JobExecutor

        slow_handler_started = False
        slow_handler_done = False
        fast_task_done = False

        def slow_sync_handler(job):
            nonlocal slow_handler_started, slow_handler_done
            slow_handler_started = True
            time.sleep(0.2)  # Simulate CPU work
            slow_handler_done = True
            return {"output": "slow"}

        async def fast_async_task():
            nonlocal fast_task_done
            await asyncio.sleep(0.05)
            fast_task_done = True

        executor = JobExecutor()
        job = {"id": "test-123", "input": {}}

        # Start slow handler and fast task concurrently
        slow_task = asyncio.create_task(executor.execute(slow_sync_handler, job))
        fast_task = asyncio.create_task(fast_async_task())

        # Wait for fast task
        await fast_task

        # Fast task should complete before slow handler
        assert fast_task_done is True
        assert slow_handler_started is True
        assert slow_handler_done is False  # Still running

        # Wait for slow handler to complete
        await slow_task
        assert slow_handler_done is True


class TestConcurrentSyncHandlers:
    """Test concurrent execution of multiple sync handlers."""

    @pytest.mark.asyncio
    async def test_multiple_sync_handlers_run_concurrently(self):
        """Multiple sync handlers can run concurrently in thread pool."""
        from runpod.serverless.core.executor import JobExecutor

        execution_times = []

        def blocking_handler(job):
            start = time.perf_counter()
            time.sleep(0.1)  # Simulate CPU work
            duration = time.perf_counter() - start
            execution_times.append(duration)
            return {"output": f"job-{job['id']}"}

        executor = JobExecutor(max_workers=3)
        jobs = [{"id": str(i), "input": {}} for i in range(3)]

        # Execute 3 handlers concurrently
        start = time.perf_counter()
        results = await asyncio.gather(*[
            executor.execute(blocking_handler, job) for job in jobs
        ])
        total_duration = time.perf_counter() - start

        # Should complete in ~0.1s (concurrent), not 0.3s (sequential)
        assert total_duration < 0.15  # Margin for overhead
        assert len(results) == 3


class TestErrorHandling:
    """Test error handling in both async and sync handlers."""

    @pytest.mark.asyncio
    async def test_async_handler_error_propagated(self):
        """Errors in async handlers are propagated."""
        from runpod.serverless.core.executor import JobExecutor

        async def failing_async_handler(job):
            raise ValueError("Async handler error")

        executor = JobExecutor()
        job = {"id": "test-123", "input": {}}

        with pytest.raises(ValueError, match="Async handler error"):
            await executor.execute(failing_async_handler, job)

    @pytest.mark.asyncio
    async def test_sync_handler_error_propagated(self):
        """Errors in sync handlers are propagated."""
        from runpod.serverless.core.executor import JobExecutor

        def failing_sync_handler(job):
            raise ValueError("Sync handler error")

        executor = JobExecutor()
        job = {"id": "test-123", "input": {}}

        with pytest.raises(ValueError, match="Sync handler error"):
            await executor.execute(failing_sync_handler, job)


class TestThreadPoolManagement:
    """Test thread pool lifecycle management."""

    def test_thread_pool_created_on_init(self):
        """Thread pool is created on initialization."""
        from runpod.serverless.core.executor import JobExecutor

        executor = JobExecutor(max_workers=5)

        assert executor._executor is not None

    def test_thread_pool_shutdown(self):
        """Thread pool can be shut down gracefully."""
        from runpod.serverless.core.executor import JobExecutor

        executor = JobExecutor(max_workers=2)

        # Should not raise
        executor.shutdown()

    @pytest.mark.asyncio
    async def test_thread_pool_rejects_after_shutdown(self):
        """Thread pool rejects new work after shutdown."""
        from runpod.serverless.core.executor import JobExecutor

        def sync_handler(job):
            return {"output": "done"}

        executor = JobExecutor()
        executor.shutdown()

        job = {"id": "test-123", "input": {}}

        # Should raise RuntimeError
        with pytest.raises(RuntimeError):
            await executor.execute(sync_handler, job)


class TestIntegrationWithJobScaler:
    """Test integration with JobScaler."""

    @pytest.mark.asyncio
    async def test_job_scaler_uses_executor_for_sync_handlers(self, mock_session, tmp_path):
        """JobScaler automatically uses executor for sync handlers."""
        from runpod.serverless.core.job_scaler import JobScaler
        from runpod.serverless.core.job_state import JobState
        import threading

        handler_thread_id = None

        def blocking_handler(job):
            nonlocal handler_thread_id
            handler_thread_id = threading.current_thread().ident
            time.sleep(0.05)
            return {"output": "blocked"}

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        scaler = JobScaler(
            concurrency=1,
            handler=blocking_handler,
            job_state=job_state,
            session=mock_session,
            job_fetch_url="http://test/job-take",
            result_url="http://test/job-done"
        )

        test_job = {"id": "test-123", "input": {}}

        # Execute job
        await scaler.semaphore.acquire()
        await scaler._process_job(test_job)

        # Handler should have run in different thread
        assert handler_thread_id != threading.current_thread().ident
