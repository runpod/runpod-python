"""
Mock Runpod API Backend for Testing.

Simulates the Runpod serverless API endpoints to enable local testing
and development without requiring actual API connectivity.
"""

import asyncio
from collections import deque
from typing import Dict, Any, Optional, List
from datetime import datetime
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel


class JobInput(BaseModel):
    """Job input payload."""
    id: Optional[str] = None
    input: Dict[str, Any]
    mock_delay: Optional[int] = None


class JobResult(BaseModel):
    """Job completion result."""
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class MockBackend:
    """
    Mock backend simulating Runpod serverless API.

    Provides endpoints for:
    - Job acquisition (job-take, job-take-batch)
    - Job completion (job-done)
    - Heartbeat (ping)
    - Streaming output (stream)

    Maintains job lifecycle states and provides metrics.
    """

    def __init__(self, host: str = "localhost", port: int = 8080):
        self.host = host
        self.port = port
        self.app = FastAPI(title="Mock Runpod Backend")

        # Job storage
        self.job_queue: deque = deque()
        self.active_jobs: Dict[str, Dict[str, Any]] = {}
        self.completed_jobs: Dict[str, Dict[str, Any]] = {}
        self.failed_jobs: Dict[str, Dict[str, Any]] = {}

        # Metrics
        self.heartbeat_log: List[Dict[str, Any]] = []
        self.stream_log: List[Dict[str, Any]] = []

        # Concurrency control
        self._lock = asyncio.Lock()

        # Setup routes
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Configure FastAPI routes."""

        @self.app.get("/v2/{endpoint_id}/job-take/{worker_id}")
        async def job_take(endpoint_id: str, worker_id: str):
            """Fetch single job from queue."""
            async with self._lock:
                if not self.job_queue:
                    return Response(status_code=204)

                job = self.job_queue.popleft()
                job["worker_id"] = worker_id
                job["acquired_at"] = datetime.utcnow().isoformat()
                self.active_jobs[job["id"]] = job

                return job

        @self.app.get("/v2/{endpoint_id}/job-take-batch/{worker_id}")
        async def job_take_batch(
            endpoint_id: str,
            worker_id: str,
            batch_size: int = 1
        ):
            """Fetch multiple jobs from queue."""
            async with self._lock:
                if not self.job_queue:
                    return Response(status_code=204)

                jobs = []
                for _ in range(min(batch_size, len(self.job_queue))):
                    job = self.job_queue.popleft()
                    job["worker_id"] = worker_id
                    job["acquired_at"] = datetime.utcnow().isoformat()
                    self.active_jobs[job["id"]] = job
                    jobs.append(job)

                return jobs if jobs else Response(status_code=204)

        @self.app.post("/v2/{endpoint_id}/job-done/{worker_id}")
        async def job_done(
            endpoint_id: str,
            worker_id: str,
            result: JobResult
        ):
            """Mark job as completed."""
            # Extract job_id from query params (simulating real API)
            # In real implementation, this would come from request
            job_id = None
            async with self._lock:
                # Find the job by worker_id (simplified)
                for jid, job in self.active_jobs.items():
                    if job.get("worker_id") == worker_id:
                        job_id = jid
                        break

                if not job_id:
                    raise HTTPException(status_code=404, detail="Job not found")

                job = self.active_jobs.pop(job_id)
                job["completed_at"] = datetime.utcnow().isoformat()
                job["result"] = result.model_dump()

                if result.error:
                    self.failed_jobs[job_id] = job
                else:
                    self.completed_jobs[job_id] = job

                return {"status": "success", "job_id": job_id}

        @self.app.get("/v2/{endpoint_id}/ping/{worker_id}")
        async def ping(endpoint_id: str, worker_id: str, job_id: Optional[str] = None):
            """Heartbeat endpoint."""
            heartbeat_entry = {
                "worker_id": worker_id,
                "job_ids": job_id.split(",") if job_id else [],
                "timestamp": datetime.utcnow().isoformat()
            }
            self.heartbeat_log.append(heartbeat_entry)

            return {"status": "pong", "timestamp": heartbeat_entry["timestamp"]}

        @self.app.post("/v2/{endpoint_id}/stream/{worker_id}")
        async def stream(
            endpoint_id: str,
            worker_id: str,
            data: Dict[str, Any]
        ):
            """Streaming output endpoint."""
            stream_entry = {
                "worker_id": worker_id,
                "data": data,
                "timestamp": datetime.utcnow().isoformat()
            }
            self.stream_log.append(stream_entry)

            return {"status": "received"}

        @self.app.get("/metrics")
        async def metrics():
            """Expose backend metrics."""
            return {
                "queued_jobs": len(self.job_queue),
                "active_jobs": len(self.active_jobs),
                "completed_jobs": len(self.completed_jobs),
                "failed_jobs": len(self.failed_jobs),
                "heartbeat_count": len(self.heartbeat_log),
                "stream_count": len(self.stream_log)
            }

    async def add_job(self, job: Dict[str, Any]) -> None:
        """
        Add job to queue (test utility).

        Args:
            job: Job data with id, input, and optional mock_delay
        """
        async with self._lock:
            if "id" not in job:
                job["id"] = f"job-{len(self.job_queue)}"
            job["queued_at"] = datetime.utcnow().isoformat()
            self.job_queue.append(job)

    async def add_jobs(self, jobs: List[Dict[str, Any]]) -> None:
        """Add multiple jobs to queue."""
        for job in jobs:
            await self.add_job(job)

    def clear_all(self) -> None:
        """Clear all jobs and metrics (test utility)."""
        self.job_queue.clear()
        self.active_jobs.clear()
        self.completed_jobs.clear()
        self.failed_jobs.clear()
        self.heartbeat_log.clear()
        self.stream_log.clear()

    def get_job_count(self) -> Dict[str, int]:
        """Get count of jobs in each state."""
        return {
            "queued": len(self.job_queue),
            "active": len(self.active_jobs),
            "completed": len(self.completed_jobs),
            "failed": len(self.failed_jobs)
        }
