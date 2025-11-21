"""
Tests for Mock Backend.

Following TDD principles, these tests define the expected behavior
of the mock backend before implementation details are finalized.
"""

import pytest
import asyncio
from httpx import ASGITransport, AsyncClient
from .mock_backend import MockBackend


@pytest.fixture
async def backend():
    """Provide mock backend instance."""
    backend = MockBackend()
    yield backend
    backend.clear_all()


@pytest.fixture
async def client(backend):
    """Provide async HTTP client for backend."""
    transport = ASGITransport(app=backend.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestMockBackendJobAcquisition:
    """Test job acquisition endpoints."""

    @pytest.mark.asyncio
    async def test_job_take_returns_job_when_available(self, backend, client):
        """GET /job-take returns job from queue."""
        # Arrange
        test_job = {"id": "job-1", "input": {"value": 42}}
        await backend.add_job(test_job)

        # Act
        response = await client.get("/v2/test-endpoint/job-take/worker-1")

        # Assert
        assert response.status_code == 200
        job = response.json()
        assert job["id"] == "job-1"
        assert job["input"]["value"] == 42
        assert "worker_id" in job
        assert job["worker_id"] == "worker-1"

    @pytest.mark.asyncio
    async def test_job_take_returns_204_when_queue_empty(self, backend, client):
        """GET /job-take returns 204 No Content when no jobs."""
        # Act
        response = await client.get("/v2/test-endpoint/job-take/worker-1")

        # Assert
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_job_take_batch_returns_multiple_jobs(self, backend, client):
        """GET /job-take-batch?batch_size=5 returns up to 5 jobs."""
        # Arrange
        jobs = [{"id": f"job-{i}", "input": {"value": i}} for i in range(10)]
        await backend.add_jobs(jobs)

        # Act
        response = await client.get(
            "/v2/test-endpoint/job-take-batch/worker-1?batch_size=5"
        )

        # Assert
        assert response.status_code == 200
        returned_jobs = response.json()
        assert len(returned_jobs) == 5
        assert all(job["worker_id"] == "worker-1" for job in returned_jobs)

    @pytest.mark.asyncio
    async def test_job_take_batch_returns_204_when_empty(self, backend, client):
        """GET /job-take-batch returns 204 when no jobs available."""
        # Act
        response = await client.get(
            "/v2/test-endpoint/job-take-batch/worker-1?batch_size=5"
        )

        # Assert
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_concurrent_job_take_no_duplicates(self, backend, client):
        """Multiple workers don't receive same job."""
        # Arrange
        jobs = [{"id": f"job-{i}", "input": {"value": i}} for i in range(5)]
        await backend.add_jobs(jobs)

        # Act - Simulate concurrent workers
        async def fetch_job(worker_id: str):
            response = await client.get(f"/v2/test-endpoint/job-take/{worker_id}")
            if response.status_code == 200:
                return response.json()
            return None

        tasks = [fetch_job(f"worker-{i}") for i in range(5)]
        results = await asyncio.gather(*tasks)

        # Assert
        received_jobs = [r for r in results if r is not None]
        assert len(received_jobs) == 5
        job_ids = [job["id"] for job in received_jobs]
        assert len(set(job_ids)) == 5  # All unique


class TestMockBackendJobCompletion:
    """Test job completion endpoints."""

    @pytest.mark.asyncio
    async def test_job_done_marks_job_complete(self, backend, client):
        """POST /job-done marks job as completed."""
        # Arrange
        await backend.add_job({"id": "job-1", "input": {"value": 42}})
        fetch_response = await client.get("/v2/test-endpoint/job-take/worker-1")
        assert fetch_response.status_code == 200

        # Act
        completion_response = await client.post(
            "/v2/test-endpoint/job-done/worker-1",
            json={"output": {"result": 84}}
        )

        # Assert
        assert completion_response.status_code == 200
        result = completion_response.json()
        assert result["status"] == "success"
        assert "job-1" in backend.completed_jobs

    @pytest.mark.asyncio
    async def test_job_done_marks_job_failed_on_error(self, backend, client):
        """POST /job-done with error marks job as failed."""
        # Arrange
        await backend.add_job({"id": "job-1", "input": {"value": 42}})
        await client.get("/v2/test-endpoint/job-take/worker-1")

        # Act
        response = await client.post(
            "/v2/test-endpoint/job-done/worker-1",
            json={"error": "Processing failed"}
        )

        # Assert
        assert response.status_code == 200
        assert "job-1" in backend.failed_jobs

    @pytest.mark.asyncio
    async def test_job_done_returns_404_for_unknown_job(self, backend, client):
        """POST /job-done returns 404 for non-existent job."""
        # Act
        response = await client.post(
            "/v2/test-endpoint/job-done/unknown-worker",
            json={"output": {"result": 42}}
        )

        # Assert
        assert response.status_code == 404


class TestMockBackendHeartbeat:
    """Test heartbeat endpoint."""

    @pytest.mark.asyncio
    async def test_ping_accepts_heartbeat(self, backend, client):
        """GET /ping accepts heartbeat."""
        # Act
        response = await client.get("/v2/test-endpoint/ping/worker-1")

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pong"
        assert len(backend.heartbeat_log) == 1

    @pytest.mark.asyncio
    async def test_ping_accepts_job_list(self, backend, client):
        """GET /ping?job_id=1,2,3 accepts job list."""
        # Act
        response = await client.get(
            "/v2/test-endpoint/ping/worker-1?job_id=job-1,job-2,job-3"
        )

        # Assert
        assert response.status_code == 200
        assert len(backend.heartbeat_log) == 1
        heartbeat = backend.heartbeat_log[0]
        assert heartbeat["worker_id"] == "worker-1"
        assert heartbeat["job_ids"] == ["job-1", "job-2", "job-3"]

    @pytest.mark.asyncio
    async def test_multiple_pings_logged(self, backend, client):
        """Multiple heartbeats are logged."""
        # Act
        for i in range(5):
            await client.get(f"/v2/test-endpoint/ping/worker-{i}")

        # Assert
        assert len(backend.heartbeat_log) == 5


class TestMockBackendStreaming:
    """Test streaming output endpoint."""

    @pytest.mark.asyncio
    async def test_stream_accepts_partial_output(self, backend, client):
        """POST /stream accepts partial output."""
        # Act
        response = await client.post(
            "/v2/test-endpoint/stream/worker-1",
            json={"partial": "output chunk 1"}
        )

        # Assert
        assert response.status_code == 200
        assert len(backend.stream_log) == 1
        assert backend.stream_log[0]["data"]["partial"] == "output chunk 1"


class TestMockBackendMetrics:
    """Test metrics endpoint."""

    @pytest.mark.asyncio
    async def test_metrics_returns_counts(self, backend, client):
        """GET /metrics returns job counts."""
        # Arrange
        await backend.add_jobs([{"id": f"job-{i}", "input": {}} for i in range(3)])

        # Act
        response = await client.get("/metrics")

        # Assert
        assert response.status_code == 200
        metrics = response.json()
        assert metrics["queued_jobs"] == 3
        assert metrics["active_jobs"] == 0
        assert metrics["completed_jobs"] == 0


class TestMockBackendUtilities:
    """Test utility methods."""

    @pytest.mark.asyncio
    async def test_add_job_generates_id_if_missing(self, backend):
        """add_job generates ID if not provided."""
        # Act
        await backend.add_job({"input": {"value": 42}})

        # Assert
        assert len(backend.job_queue) == 1
        job = backend.job_queue[0]
        assert "id" in job
        assert job["id"].startswith("job-")

    @pytest.mark.asyncio
    async def test_clear_all_resets_state(self, backend):
        """clear_all() removes all jobs and metrics."""
        # Arrange
        await backend.add_jobs([{"id": f"job-{i}", "input": {}} for i in range(5)])
        backend.heartbeat_log.append({"test": "data"})

        # Act
        backend.clear_all()

        # Assert
        counts = backend.get_job_count()
        assert counts["queued"] == 0
        assert counts["active"] == 0
        assert counts["completed"] == 0
        assert len(backend.heartbeat_log) == 0
