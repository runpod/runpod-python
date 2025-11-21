"""
Tests for WorkerAdapter URL template substitution.

Verifies that URL templates from environment variables are properly
substituted at initialization with worker_id and gpu_type_id.
"""

import pytest
import os
from unittest.mock import patch, AsyncMock


class TestWorkerAdapterURLTemplates:
    """Test URL template variable replacement at initialization."""

    @pytest.mark.asyncio
    async def test_urls_replace_pod_id_and_gpu_type(self):
        """URLs replace $RUNPOD_POD_ID and $RUNPOD_GPU_TYPE_ID at init."""
        from runpod.serverless.core.worker_adapter import WorkerAdapter

        # Mock environment with template variables
        env_vars = {
            "RUNPOD_POD_ID": "test-pod-123",
            "RUNPOD_GPU_TYPE_ID": "RTX4090",
            "RUNPOD_WEBHOOK_GET_JOB": "https://api.runpod.ai/fetch/$RUNPOD_POD_ID?gpu=$RUNPOD_GPU_TYPE_ID",
            "RUNPOD_WEBHOOK_POST_OUTPUT": "https://api.runpod.ai/result/$RUNPOD_POD_ID/$ID?gpu=$RUNPOD_GPU_TYPE_ID",
            "RUNPOD_WEBHOOK_POST_STREAM": "https://api.runpod.ai/stream/$RUNPOD_POD_ID?gpu=$RUNPOD_GPU_TYPE_ID",
            "RUNPOD_WEBHOOK_PING": "https://api.runpod.ai/ping/$RUNPOD_POD_ID?gpu=$RUNPOD_GPU_TYPE_ID",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            async def mock_handler(job):
                return {"result": "test"}

            config = {"handler": mock_handler}
            adapter = WorkerAdapter(config)

            # Verify job_fetch_url has templates replaced
            assert "$RUNPOD_POD_ID" not in adapter.job_fetch_url
            assert "$RUNPOD_GPU_TYPE_ID" not in adapter.job_fetch_url
            assert "test-pod-123" in adapter.job_fetch_url
            assert "RTX4090" in adapter.job_fetch_url
            assert adapter.job_fetch_url == "https://api.runpod.ai/fetch/test-pod-123?gpu=RTX4090"

            # Verify result_url has templates replaced (except $ID which is per-job)
            assert "$RUNPOD_POD_ID" not in adapter.result_url
            assert "$RUNPOD_GPU_TYPE_ID" not in adapter.result_url
            assert "test-pod-123" in adapter.result_url
            assert "RTX4090" in adapter.result_url
            assert "$ID" in adapter.result_url  # This is replaced per-job
            assert adapter.result_url == "https://api.runpod.ai/result/test-pod-123/$ID?gpu=RTX4090"

            # Verify stream_url has templates replaced
            assert "$RUNPOD_POD_ID" not in adapter.stream_url
            assert "$RUNPOD_GPU_TYPE_ID" not in adapter.stream_url
            assert "test-pod-123" in adapter.stream_url
            assert "RTX4090" in adapter.stream_url
            assert adapter.stream_url == "https://api.runpod.ai/stream/test-pod-123?gpu=RTX4090"

            # Verify ping_url has templates replaced
            assert "$RUNPOD_POD_ID" not in adapter.ping_url
            assert "$RUNPOD_GPU_TYPE_ID" not in adapter.ping_url
            assert "test-pod-123" in adapter.ping_url
            assert "RTX4090" in adapter.ping_url
            assert adapter.ping_url == "https://api.runpod.ai/ping/test-pod-123?gpu=RTX4090"

    @pytest.mark.asyncio
    async def test_urls_without_templates_unchanged(self):
        """URLs without template variables are not modified."""
        from runpod.serverless.core.worker_adapter import WorkerAdapter

        # Mock environment without template variables
        env_vars = {
            "RUNPOD_POD_ID": "test-pod-456",
            "RUNPOD_GPU_TYPE_ID": "A100",
            "RUNPOD_WEBHOOK_GET_JOB": "https://api.runpod.ai/fetch",
            "RUNPOD_WEBHOOK_POST_OUTPUT": "https://api.runpod.ai/result",
            "RUNPOD_WEBHOOK_PING": "https://api.runpod.ai/ping",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            async def mock_handler(job):
                return {"result": "test"}

            config = {"handler": mock_handler}
            adapter = WorkerAdapter(config)

            # Verify URLs unchanged
            assert adapter.job_fetch_url == "https://api.runpod.ai/fetch"
            assert adapter.result_url == "https://api.runpod.ai/result"
            assert adapter.ping_url == "https://api.runpod.ai/ping"

    @pytest.mark.asyncio
    async def test_urls_with_missing_env_vars_use_unknown(self):
        """URLs use 'unknown' when env vars not set."""
        from runpod.serverless.core.worker_adapter import WorkerAdapter

        # Mock environment with template but missing actual values
        env_vars = {
            "RUNPOD_WEBHOOK_GET_JOB": "https://api.runpod.ai/fetch/$RUNPOD_POD_ID?gpu=$RUNPOD_GPU_TYPE_ID",
        }

        # Temporarily remove RUNPOD_POD_ID and RUNPOD_GPU_TYPE_ID
        with patch.dict(os.environ, env_vars, clear=False):
            # Ensure the variables don't exist
            os.environ.pop("RUNPOD_POD_ID", None)
            os.environ.pop("RUNPOD_GPU_TYPE_ID", None)

            async def mock_handler(job):
                return {"result": "test"}

            config = {"handler": mock_handler}
            adapter = WorkerAdapter(config)

            # Verify 'unknown' used as fallback
            assert "unknown" in adapter.job_fetch_url
            assert adapter.job_fetch_url == "https://api.runpod.ai/fetch/unknown?gpu=unknown"
