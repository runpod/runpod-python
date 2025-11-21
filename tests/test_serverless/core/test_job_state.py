"""
Tests for JobState - In-Memory State Management.

Following TDD principles, these tests define performance and functionality
requirements for the in-memory job state system with async checkpointing.

Performance Target: <1ms for add/remove operations (1000x faster than current 5-15ms)
"""

import pytest
import asyncio
import time
import pickle


# Import will be available after implementation
# from runpod.serverless.core.job_state import JobState, Job


class TestJobDataClass:
    """Test Job dataclass."""

    def test_job_creation_with_id(self):
        """Job can be created with ID."""
        from runpod.serverless.core.job_state import Job

        job = Job(id="test-123")
        assert job.id == "test-123"
        assert job.input is None
        assert job.webhook is None

    def test_job_creation_with_all_fields(self):
        """Job can be created with all fields."""
        from runpod.serverless.core.job_state import Job

        job = Job(id="test-123", input={"data": "test"}, webhook="http://test")
        assert job.id == "test-123"
        assert job.input == {"data": "test"}
        assert job.webhook == "http://test"

    def test_job_equality(self):
        """Jobs are equal if IDs match."""
        from runpod.serverless.core.job_state import Job

        job1 = Job(id="test-123", input={"a": 1})
        job2 = Job(id="test-123", input={"b": 2})
        assert job1 == job2

    def test_job_hashable(self):
        """Jobs can be used in sets."""
        from runpod.serverless.core.job_state import Job

        job1 = Job(id="test-1")
        job2 = Job(id="test-2")
        job_set = {job1, job2}
        assert len(job_set) == 2


class TestJobStateInMemoryOperations:
    """Test in-memory operations are non-blocking and fast."""

    @pytest.mark.asyncio
    async def test_add_job_is_nonblocking(self, tmp_path):
        """Adding job completes in <1ms."""
        from runpod.serverless.core.job_state import JobState, Job

        state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        job = Job(id="test-1")

        start = time.perf_counter()
        await state.add(job)
        duration = time.perf_counter() - start

        assert duration < 0.001  # <1ms
        assert job in state

    @pytest.mark.asyncio
    async def test_remove_job_is_nonblocking(self, tmp_path):
        """Removing job completes in <1ms."""
        from runpod.serverless.core.job_state import JobState, Job

        state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        job = Job(id="test-1")
        await state.add(job)

        start = time.perf_counter()
        await state.remove(job)
        duration = time.perf_counter() - start

        assert duration < 0.001  # <1ms
        assert job not in state

    @pytest.mark.asyncio
    async def test_add_multiple_jobs_fast(self, tmp_path):
        """Adding 100 jobs completes in <100ms."""
        from runpod.serverless.core.job_state import JobState, Job

        state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        jobs = [Job(id=f"job-{i}") for i in range(100)]

        start = time.perf_counter()
        for job in jobs:
            await state.add(job)
        duration = time.perf_counter() - start

        assert duration < 0.1  # <100ms for 100 jobs
        assert len(state) == 100

    @pytest.mark.asyncio
    async def test_get_job_list_no_file_io(self, tmp_path, mocker):
        """get_job_list reads from memory, not disk."""
        from runpod.serverless.core.job_state import JobState, Job

        state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        await state.add(Job(id="job-1"))
        await state.add(Job(id="job-2"))

        # Mock file open to ensure it's not called
        mock_open = mocker.patch("builtins.open")

        job_list = state.get_job_list()

        assert "job-1" in job_list
        assert "job-2" in job_list
        mock_open.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_job_count(self, tmp_path):
        """get_job_count returns accurate count."""
        from runpod.serverless.core.job_state import JobState, Job

        state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        assert state.get_job_count() == 0

        await state.add(Job(id="job-1"))
        await state.add(Job(id="job-2"))
        assert state.get_job_count() == 2

        await state.remove(Job(id="job-1"))
        assert state.get_job_count() == 1


class TestJobStateCheckpointing:
    """Test async checkpointing functionality."""

    @pytest.mark.asyncio
    async def test_checkpoint_persists_to_disk(self, tmp_path):
        """Checkpoint writes state to disk."""
        from runpod.serverless.core.job_state import JobState, Job

        checkpoint_path = tmp_path / "jobs.pkl"
        state = JobState(checkpoint_path=checkpoint_path)

        await state.add(Job(id="test-1"))
        await state.add(Job(id="test-2"))

        # Force immediate checkpoint
        await state._checkpoint_now()

        # Verify file exists
        assert checkpoint_path.exists()

        # Verify contents
        with open(checkpoint_path, "rb") as f:
            jobs = pickle.load(f)
        assert len(jobs) == 2
        job_ids = {job.id for job in jobs}
        assert "test-1" in job_ids
        assert "test-2" in job_ids

    @pytest.mark.asyncio
    async def test_load_from_checkpoint(self, tmp_path):
        """State can be restored from checkpoint."""
        from runpod.serverless.core.job_state import JobState, Job

        checkpoint_path = tmp_path / "jobs.pkl"

        # Create and checkpoint state
        state1 = JobState(checkpoint_path=checkpoint_path)
        await state1.add(Job(id="test-1"))
        await state1.add(Job(id="test-2"))
        await state1._checkpoint_now()

        # Create new instance and load
        state2 = JobState(checkpoint_path=checkpoint_path)
        await state2.load_from_checkpoint()

        assert state2.get_job_count() == 2
        assert Job(id="test-1") in state2
        assert Job(id="test-2") in state2

    @pytest.mark.asyncio
    async def test_checkpoint_loop_runs_periodically(self, tmp_path, mocker):
        """Background checkpoint task runs on interval."""
        from runpod.serverless.core.job_state import JobState, Job

        state = JobState(checkpoint_path=tmp_path / "jobs.pkl", checkpoint_interval=0.1)
        mock_write = mocker.patch.object(state, "_write_checkpoint")

        await state.add(Job(id="test-1"))  # Mark dirty
        await state.start_checkpoint_task()

        # Wait for at least 2 checkpoint intervals
        await asyncio.sleep(0.25)

        # Should have checkpointed at least once
        assert mock_write.call_count >= 1

        # Stop checkpoint task
        if state._checkpoint_task:
            state._checkpoint_task.cancel()
            try:
                await state._checkpoint_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_checkpoint_only_when_dirty(self, tmp_path, mocker):
        """Checkpoint only writes when state has changed."""
        from runpod.serverless.core.job_state import JobState

        state = JobState(checkpoint_path=tmp_path / "jobs.pkl", checkpoint_interval=0.1)
        mock_write = mocker.patch.object(state, "_write_checkpoint")

        # Start checkpoint without any changes
        await state.start_checkpoint_task()
        await asyncio.sleep(0.25)

        # Should not checkpoint if no changes
        assert mock_write.call_count == 0

        # Stop task
        if state._checkpoint_task:
            state._checkpoint_task.cancel()
            try:
                await state._checkpoint_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_atomic_checkpoint_write(self, tmp_path):
        """Checkpoint uses atomic write (temp + rename)."""
        from runpod.serverless.core.job_state import JobState, Job

        checkpoint_path = tmp_path / "jobs.pkl"
        state = JobState(checkpoint_path=checkpoint_path)

        await state.add(Job(id="test-1"))

        # Checkpoint should create temp file then rename
        await state._checkpoint_now()

        assert checkpoint_path.exists()
        # Temp file should be cleaned up
        assert not (tmp_path / "jobs.pkl.tmp").exists()


class TestJobStateConcurrency:
    """Test thread-safety with async locks."""

    @pytest.mark.asyncio
    async def test_concurrent_adds_are_safe(self, tmp_path):
        """Multiple concurrent adds don't corrupt state."""
        from runpod.serverless.core.job_state import JobState, Job

        state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        async def add_jobs(start_idx: int, count: int):
            for i in range(start_idx, start_idx + count):
                await state.add(Job(id=f"job-{i}"))

        # Run 5 concurrent tasks each adding 20 jobs
        tasks = [add_jobs(i * 20, 20) for i in range(5)]
        await asyncio.gather(*tasks)

        assert state.get_job_count() == 100

    @pytest.mark.asyncio
    async def test_concurrent_add_remove_are_safe(self, tmp_path):
        """Concurrent adds and removes maintain consistency."""
        from runpod.serverless.core.job_state import JobState, Job

        state = JobState(checkpoint_path=tmp_path / "jobs.pkl")

        # Pre-populate
        for i in range(50):
            await state.add(Job(id=f"job-{i}"))

        async def add_jobs():
            for i in range(50, 100):
                await state.add(Job(id=f"job-{i}"))

        async def remove_jobs():
            for i in range(25):
                await state.remove(Job(id=f"job-{i}"))

        # Run add and remove concurrently
        await asyncio.gather(add_jobs(), remove_jobs())

        # Should have 50 (initial) - 25 (removed) + 50 (added) = 75
        assert state.get_job_count() == 75


class TestJobStateEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_remove_nonexistent_job_no_error(self, tmp_path):
        """Removing non-existent job doesn't raise error."""
        from runpod.serverless.core.job_state import JobState, Job

        state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        job = Job(id="nonexistent")

        # Should not raise
        await state.remove(job)
        assert state.get_job_count() == 0

    @pytest.mark.asyncio
    async def test_get_job_list_when_empty(self, tmp_path):
        """get_job_list returns None when empty."""
        from runpod.serverless.core.job_state import JobState

        state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        assert state.get_job_list() is None

    @pytest.mark.asyncio
    async def test_load_from_nonexistent_checkpoint(self, tmp_path):
        """Loading from non-existent checkpoint doesn't error."""
        from runpod.serverless.core.job_state import JobState

        state = JobState(checkpoint_path=tmp_path / "nonexistent.pkl")

        # Should not raise
        await state.load_from_checkpoint()
        assert state.get_job_count() == 0

    @pytest.mark.asyncio
    async def test_contains_check(self, tmp_path):
        """Can check if job is in state with 'in' operator."""
        from runpod.serverless.core.job_state import JobState, Job

        state = JobState(checkpoint_path=tmp_path / "jobs.pkl")
        job = Job(id="test-1")

        assert job not in state
        await state.add(job)
        assert job in state
        await state.remove(job)
        assert job not in state
