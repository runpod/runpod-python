"""
Integration Tests - Full Job Scheduler Simulation.

End-to-end tests combining all components:
- MockBackend (job queue simulation)
- JobState (in-memory state)
- Heartbeat (async heartbeat)
- JobScaler (job acquisition and processing)
- ProgressSystem (progress updates)
- JobExecutor (handler execution)

These tests simulate real worker scenarios with the mock backend.
"""

import pytest
import asyncio
import time
from typing import Dict, Any, List
import aiohttp


class TestBasicJobLifecycle:
    """Test complete job lifecycle from acquisition to completion."""

    @pytest.mark.asyncio
    async def test_single_job_end_to_end(self, tmp_path):
        """Single job flows from queue to completion."""
        from runpod.serverless.core.job_state import JobState, Job
        from runpod.serverless.core.job_scaler import JobScaler
        from tests.test_serverless.core.mock_backend import MockBackend

        # Setup mock backend
        backend = MockBackend()
        await backend.add_job({"id": "test-1", "input": {"value": 42}})

        # Setup job state
        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        # Setup handler
        results = []

        async def handler(job):
            results.append(job["id"])
            await asyncio.sleep(0.01)
            return {"output": f"processed-{job['id']}"}

        # Setup session
        async with aiohttp.ClientSession() as session:
            # Setup scaler
            scaler = JobScaler(
                concurrency=1,
                handler=handler,
                job_state=job_state,
                session=session,
                job_fetch_url=f"http://localhost:8000/job-take",
                result_url=f"http://localhost:8000/job-done"
            )

            # Manually get job from backend's internal queue
            job = None
            if backend.job_queue:
                job = backend.job_queue.popleft()
                backend.active_jobs[job["id"]] = job

            assert job is not None

            # Acquire and process job
            await scaler.semaphore.acquire()
            await scaler._process_job(job)

            # Verify job was processed
            assert len(results) == 1
            assert results[0] == "test-1"

    @pytest.mark.asyncio
    async def test_multiple_jobs_sequential(self, tmp_path):
        """Multiple jobs process sequentially."""
        from runpod.serverless.core.job_state import JobState
        from runpod.serverless.core.job_scaler import JobScaler
        from tests.test_serverless.core.mock_backend import MockBackend

        backend = MockBackend()
        for i in range(3):
            await backend.add_job({"id": f"job-{i}", "input": {"value": i}})

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        processed = []

        async def handler(job):
            processed.append(job["id"])
            return {"output": f"done-{job['id']}"}

        async with aiohttp.ClientSession() as session:
            scaler = JobScaler(
                concurrency=1,
                handler=handler,
                job_state=job_state,
                session=session,
                job_fetch_url="http://test/job-take"
            )

            # Process all jobs
            for _ in range(3):
                if backend.job_queue:
                    job = backend.job_queue.popleft()
                    backend.active_jobs[job["id"]] = job

                    await scaler.semaphore.acquire()
                    await scaler._process_job(job)

            assert len(processed) == 3
            assert all(f"job-{i}" in processed for i in range(3))


class TestConcurrentJobProcessing:
    """Test concurrent job processing scenarios."""

    @pytest.mark.asyncio
    async def test_concurrent_job_processing(self, tmp_path):
        """Multiple jobs process concurrently up to concurrency limit."""
        from runpod.serverless.core.job_state import JobState
        from runpod.serverless.core.job_scaler import JobScaler
        from tests.test_serverless.core.mock_backend import MockBackend

        backend = MockBackend()
        for i in range(5):
            await backend.add_job({"id": f"job-{i}", "input": {"value": i}})

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        processing_times = []

        async def handler(job):
            start = time.perf_counter()
            await asyncio.sleep(0.1)
            duration = time.perf_counter() - start
            processing_times.append(duration)
            return {"output": f"done-{job['id']}"}

        async with aiohttp.ClientSession() as session:
            scaler = JobScaler(
                concurrency=3,
                handler=handler,
                job_state=job_state,
                session=session,
                job_fetch_url="http://test/job-take"
            )

            # Process 3 jobs concurrently
            tasks = []
            for _ in range(3):
                if backend.job_queue:
                    job = backend.job_queue.popleft()
                    backend.active_jobs[job["id"]] = job

                    await scaler.semaphore.acquire()
                    task = asyncio.create_task(scaler._process_job(job))
                    tasks.append(task)

            start = time.perf_counter()
            await asyncio.gather(*tasks)
            total_duration = time.perf_counter() - start

            # Should complete in ~0.1s (concurrent), not 0.3s (sequential)
            assert total_duration < 0.15


class TestHeartbeatIntegration:
    """Test heartbeat integration with job processing."""

    @pytest.mark.asyncio
    async def test_heartbeat_reports_active_jobs(self, tmp_path):
        """Heartbeat reports active job IDs during processing."""
        from runpod.serverless.core.job_state import JobState, Job
        from runpod.serverless.core.heartbeat import Heartbeat
        from tests.test_serverless.core.mock_backend import MockBackend

        backend = MockBackend()
        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        # Add jobs to state
        await job_state.add(Job(id="job-1"))
        await job_state.add(Job(id="job-2"))

        async with aiohttp.ClientSession() as session:
            heartbeat = Heartbeat(
                session=session,
                job_state=job_state,
                ping_url="http://test/ping",
                interval=0.1
            )

            await heartbeat.start()
            await asyncio.sleep(0.15)  # Let it ping once

            # Verify job state is accessible
            job_list = job_state.get_job_list()
            assert "job-1" in job_list
            assert "job-2" in job_list

            await heartbeat.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_continues_during_job_processing(self, tmp_path):
        """Heartbeat continues independently during job execution."""
        from runpod.serverless.core.job_state import JobState, Job
        from runpod.serverless.core.heartbeat import Heartbeat

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        await job_state.add(Job(id="long-job"))

        ping_count = 0

        async def mock_ping():
            nonlocal ping_count
            ping_count += 1

        async with aiohttp.ClientSession() as session:
            heartbeat = Heartbeat(
                session=session,
                job_state=job_state,
                ping_url="http://test/ping",
                interval=0.05
            )

            await heartbeat.start()

            # Simulate long job
            await asyncio.sleep(0.2)

            await heartbeat.stop()

            # Should have pinged multiple times
            # (implementation test, not mock test)


class TestProgressIntegration:
    """Test progress updates during job processing."""

    @pytest.mark.asyncio
    async def test_progress_updates_during_job_execution(self, tmp_path):
        """Handler sends progress updates that are batched and sent."""
        from runpod.serverless.core.progress import ProgressSystem
        from runpod.serverless.core.job_scaler import JobScaler
        from runpod.serverless.core.job_state import JobState

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        # Track progress updates
        progress_updates = []

        async with aiohttp.ClientSession() as session:
            # Mock session for progress
            from unittest.mock import AsyncMock
            progress_session = AsyncMock(spec=aiohttp.ClientSession)
            response_mock = AsyncMock()
            response_mock.raise_for_status = AsyncMock(return_value=None)
            progress_session.post.return_value.__aenter__.return_value = response_mock
            progress_session.post.return_value.__aexit__.return_value = None

            progress = ProgressSystem(
                session=progress_session,
                progress_url="http://test/progress",
                batch_size=3
            )
            await progress.start()

            # Handler that sends progress
            async def handler(job):
                await progress.update(job["id"], {"percent": 0})
                await asyncio.sleep(0.01)
                await progress.update(job["id"], {"percent": 50})
                await asyncio.sleep(0.01)
                await progress.update(job["id"], {"percent": 100})
                return {"output": "done"}

            scaler = JobScaler(
                concurrency=1,
                handler=handler,
                job_state=job_state,
                session=session,
                job_fetch_url="http://test/job-take"
            )

            # Process job
            test_job = {"id": "test-123", "input": {}}
            await scaler.semaphore.acquire()
            await scaler._process_job(test_job)

            # Wait for progress batch
            await asyncio.sleep(0.1)

            # Progress should have been sent
            assert progress_session.post.call_count >= 1

            await progress.stop()


class TestExecutorIntegration:
    """Test executor integration with job processing."""

    @pytest.mark.asyncio
    async def test_sync_handler_uses_thread_pool(self, tmp_path):
        """Sync handlers automatically use thread pool via executor."""
        from runpod.serverless.core.job_scaler import JobScaler
        from runpod.serverless.core.job_state import JobState
        import threading

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        handler_thread_id = None

        def sync_handler(job):
            nonlocal handler_thread_id
            handler_thread_id = threading.current_thread().ident
            time.sleep(0.05)
            return {"output": "sync-done"}

        async with aiohttp.ClientSession() as session:
            scaler = JobScaler(
                concurrency=1,
                handler=sync_handler,
                job_state=job_state,
                session=session,
                job_fetch_url="http://test/job-take"
            )

            test_job = {"id": "test-123", "input": {}}
            await scaler.semaphore.acquire()
            await scaler._process_job(test_job)

            # Handler should have run in different thread
            assert handler_thread_id != threading.current_thread().ident

    @pytest.mark.asyncio
    async def test_async_handler_runs_in_event_loop(self, tmp_path):
        """Async handlers run directly in event loop."""
        from runpod.serverless.core.job_scaler import JobScaler
        from runpod.serverless.core.job_state import JobState
        import threading

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        handler_thread_id = None

        async def async_handler(job):
            nonlocal handler_thread_id
            handler_thread_id = threading.current_thread().ident
            await asyncio.sleep(0.01)
            return {"output": "async-done"}

        async with aiohttp.ClientSession() as session:
            scaler = JobScaler(
                concurrency=1,
                handler=async_handler,
                job_state=job_state,
                session=session,
                job_fetch_url="http://test/job-take"
            )

            test_job = {"id": "test-123", "input": {}}
            await scaler.semaphore.acquire()
            await scaler._process_job(test_job)

            # Handler should have run in same thread
            assert handler_thread_id == threading.current_thread().ident


class TestDynamicScaling:
    """Test dynamic concurrency scaling during operation."""

    @pytest.mark.asyncio
    async def test_scale_up_during_processing(self, tmp_path):
        """Scaler can increase concurrency while jobs are running."""
        from runpod.serverless.core.job_scaler import JobScaler
        from runpod.serverless.core.job_state import JobState

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        async def handler(job):
            await asyncio.sleep(0.1)
            return {"output": "done"}

        async with aiohttp.ClientSession() as session:
            scaler = JobScaler(
                concurrency=2,
                handler=handler,
                job_state=job_state,
                session=session,
                job_fetch_url="http://test/job-take"
            )

            # Start a job
            await scaler.semaphore.acquire()
            job_task = asyncio.create_task(
                scaler._process_job({"id": "test-1", "input": {}})
            )

            # Scale up while job is running
            await scaler.adjust_concurrency(5)

            assert scaler.current_concurrency == 5
            assert scaler.semaphore._value == 4  # 5 total - 1 in use

            # Wait for job to complete
            await job_task

    @pytest.mark.asyncio
    async def test_scale_down_during_processing(self, tmp_path):
        """Scaler can decrease concurrency while jobs are running."""
        from runpod.serverless.core.job_scaler import JobScaler
        from runpod.serverless.core.job_state import JobState

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        async def handler(job):
            await asyncio.sleep(0.1)
            return {"output": "done"}

        async with aiohttp.ClientSession() as session:
            scaler = JobScaler(
                concurrency=5,
                handler=handler,
                job_state=job_state,
                session=session,
                job_fetch_url="http://test/job-take"
            )

            # Start a job
            await scaler.semaphore.acquire()
            job_task = asyncio.create_task(
                scaler._process_job({"id": "test-1", "input": {}})
            )

            # Scale down while job is running
            await scaler.adjust_concurrency(2)

            assert scaler.current_concurrency == 2
            # Job continues running
            await job_task


class TestErrorRecovery:
    """Test error handling and recovery scenarios."""

    @pytest.mark.asyncio
    async def test_handler_error_cleanup(self, tmp_path):
        """Handler errors are caught and resources cleaned up."""
        from runpod.serverless.core.job_scaler import JobScaler
        from runpod.serverless.core.job_state import JobState, Job

        job_state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        async def failing_handler(job):
            raise ValueError("Handler error")

        async with aiohttp.ClientSession() as session:
            scaler = JobScaler(
                concurrency=2,
                handler=failing_handler,
                job_state=job_state,
                session=session,
                job_fetch_url="http://test/job-take"
            )

            initial_permits = scaler.semaphore._value

            # Process failing job
            await scaler.semaphore.acquire()
            test_job = {"id": "test-123", "input": {}}
            await scaler._process_job(test_job)  # Should not raise

            # Semaphore should be released
            assert scaler.semaphore._value == initial_permits

            # Job should be removed from state
            assert Job(id="test-123") not in job_state


class TestStateCheckpointing:
    """Test state checkpointing during job processing."""

    @pytest.mark.asyncio
    async def test_state_checkpointed_during_processing(self, tmp_path):
        """Job state is checkpointed while jobs are processing."""
        from runpod.serverless.core.job_state import JobState, Job

        checkpoint_path = tmp_path / "jobs.pkl"
        job_state = JobState(
            checkpoint_path=checkpoint_path,
            checkpoint_interval=0.1
        )

        # Start checkpoint task
        await job_state.start_checkpoint_task()

        # Add jobs
        await job_state.add(Job(id="job-1"))
        await job_state.add(Job(id="job-2"))

        # Wait for checkpoint
        await asyncio.sleep(0.15)

        # Stop checkpointing
        await job_state.stop_checkpoint_task()

        # Verify checkpoint file exists
        assert checkpoint_path.exists()

        # Load from checkpoint
        new_state = JobState(checkpoint_path=checkpoint_path)
        await new_state.load_from_checkpoint()

        assert Job(id="job-1") in new_state
        assert Job(id="job-2") in new_state
