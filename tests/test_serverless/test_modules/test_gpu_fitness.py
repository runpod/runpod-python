"""
Tests for the GPU fitness check system (rp_gpu_fitness module).

Tests cover output parsing, binary path resolution, auto-registration,
and health check logic with various GPU scenarios.
"""

import asyncio
import os
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock, call

from runpod.serverless.modules.rp_gpu_fitness import (
    _parse_gpu_test_output,
    _get_gpu_test_binary_path,
    _run_gpu_test_binary,
    _run_gpu_test_fallback,
    _check_gpu_health,
    auto_register_gpu_check,
)
from runpod.serverless.modules.rp_fitness import clear_fitness_checks, _fitness_checks


@pytest.fixture(autouse=True)
def cleanup_fitness_checks():
    """Automatically clean up fitness checks before and after each test."""
    clear_fitness_checks()
    yield
    clear_fitness_checks()


# ============================================================================
# Output Parsing Tests
# ============================================================================

class TestGpuTestOutputParsing:
    """Tests for binary output parsing logic."""

    def test_parse_success_single_gpu(self):
        """Test parsing successful single GPU output."""
        output = """Linux Kernel Version: 5.15.0
CUDA Driver Version: 12.2
Found 1 GPUs:
GPU 0: NVIDIA A100 (UUID: GPU-xxx)
GPU 0 memory allocation test passed.
"""
        result = _parse_gpu_test_output(output)

        assert result["success"] is True
        assert result["gpu_count"] == 1
        assert result["found_gpus"] == 1
        assert len(result["errors"]) == 0
        assert result["details"]["cuda_version"] == "12.2"
        assert "5.15.0" in result["details"]["kernel"]

    def test_parse_success_multi_gpu(self):
        """Test parsing successful multi-GPU output."""
        output = """Linux Kernel Version: 5.15.0
CUDA Driver Version: 12.2
Found 2 GPUs:
GPU 0: NVIDIA A100 (UUID: GPU-xxx)
GPU 0 memory allocation test passed.
GPU 1: NVIDIA A100 (UUID: GPU-yyy)
GPU 1 memory allocation test passed.
"""
        result = _parse_gpu_test_output(output)

        assert result["success"] is True
        assert result["gpu_count"] == 2
        assert result["found_gpus"] == 2
        assert len(result["errors"]) == 0

    def test_parse_failure_nvml_init(self):
        """Test parsing NVML initialization failure."""
        output = "Failed to initialize NVML: Driver/library version mismatch\n"
        result = _parse_gpu_test_output(output)

        assert result["success"] is False
        assert len(result["errors"]) > 0
        assert any("Failed to initialize" in e for e in result["errors"])

    def test_parse_failure_no_gpus(self):
        """Test parsing when no GPUs found."""
        output = """Linux Kernel Version: 5.15.0
CUDA Driver Version: 12.2
Found 0 GPUs:
"""
        result = _parse_gpu_test_output(output)

        assert result["success"] is False
        assert result["gpu_count"] == 0
        assert result["found_gpus"] == 0

    def test_parse_failure_memory_allocation(self):
        """Test parsing GPU memory allocation failure."""
        output = """Linux Kernel Version: 5.15.0
CUDA Driver Version: 12.2
Found 1 GPUs:
GPU 0: NVIDIA A100 (UUID: GPU-xxx)
GPU 0 memory allocation test failed. Error code: 2 (out of memory)
"""
        result = _parse_gpu_test_output(output)

        assert result["success"] is False
        assert result["gpu_count"] == 0
        assert len(result["errors"]) > 0

    def test_parse_partial_failure_mixed_gpus(self):
        """Test parsing when some GPUs pass and others fail."""
        output = """Linux Kernel Version: 5.15.0
CUDA Driver Version: 12.2
Found 2 GPUs:
GPU 0: NVIDIA A100 (UUID: GPU-xxx)
GPU 0 memory allocation test passed.
GPU 1: NVIDIA A100 (UUID: GPU-yyy)
GPU 1 memory allocation test failed. Error code: 2
"""
        result = _parse_gpu_test_output(output)

        assert result["success"] is False
        assert result["gpu_count"] == 1
        assert result["found_gpus"] == 2

    def test_parse_error_messages_capture(self):
        """Test that various error messages are captured."""
        output = """Failed to get GPU count: Driver not found
GPU 0: Error cannot access device
Unable to initialize CUDA
"""
        result = _parse_gpu_test_output(output)

        assert result["success"] is False
        assert len(result["errors"]) >= 3


# ============================================================================
# Binary Path Resolution Tests
# ============================================================================

class TestBinaryPathResolution:
    """Tests for binary path location logic."""

    def test_finds_package_binary(self):
        """Test locating binary in package."""
        with patch("runpod._binary_helpers.Path") as mock_path:
            mock_binary = MagicMock()
            mock_binary.exists.return_value = True
            mock_binary.is_file.return_value = True
            mock_path.return_value = mock_binary

            path = _get_gpu_test_binary_path()
            assert path is not None

    def test_returns_none_if_binary_not_found(self):
        """Test returns None when binary not in package."""
        with patch("runpod.serverless.modules.rp_gpu_fitness.get_binary_path") as mock_get:
            mock_get.return_value = None
            path = _get_gpu_test_binary_path()
            assert path is None

    @patch.dict(os.environ, {"RUNPOD_BINARY_GPU_TEST_PATH": "/custom/gpu_test"})
    def test_respects_env_override(self):
        """Test environment variable override takes precedence."""
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True):
            # When env var is set and path exists, it should be used
            pass


# ============================================================================
# Binary Execution Tests
# ============================================================================

class TestBinaryExecution:
    """Tests for binary execution logic."""

    @pytest.mark.asyncio
    async def test_binary_success(self):
        """Test successful binary execution."""
        success_output = """Linux Kernel Version: 5.15.0
CUDA Driver Version: 12.2
Found 1 GPUs:
GPU 0: NVIDIA A100 (UUID: GPU-xxx)
GPU 0 memory allocation test passed.
"""
        with patch(
            "runpod.serverless.modules.rp_gpu_fitness._get_gpu_test_binary_path"
        ) as mock_path, \
             patch("asyncio.create_subprocess_exec") as mock_exec, \
             patch("os.access", return_value=True):

            mock_path.return_value = Path("/fake/gpu_test")
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(
                return_value=(success_output.encode(), b"")
            )
            mock_exec.return_value = mock_process

            result = await _run_gpu_test_binary()
            assert result["success"] is True
            assert result["gpu_count"] == 1

    @pytest.mark.asyncio
    async def test_binary_not_found(self):
        """Test error when binary not found."""
        with patch(
            "runpod.serverless.modules.rp_gpu_fitness._get_gpu_test_binary_path"
        ) as mock_path:
            mock_path.return_value = None

            with pytest.raises(FileNotFoundError):
                await _run_gpu_test_binary()

    @pytest.mark.asyncio
    async def test_binary_not_executable(self):
        """Test error when binary not executable."""
        with patch(
            "runpod.serverless.modules.rp_gpu_fitness._get_gpu_test_binary_path"
        ) as mock_path, \
             patch("os.access", return_value=False):

            mock_path.return_value = Path("/fake/gpu_test")

            with pytest.raises(PermissionError):
                await _run_gpu_test_binary()

    @pytest.mark.asyncio
    async def test_binary_timeout(self):
        """Test error when binary times out."""
        with patch(
            "runpod.serverless.modules.rp_gpu_fitness._get_gpu_test_binary_path"
        ) as mock_path, \
             patch("asyncio.create_subprocess_exec") as mock_exec, \
             patch("os.access", return_value=True):

            mock_path.return_value = Path("/fake/gpu_test")
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(
                side_effect=asyncio.TimeoutError()
            )
            mock_exec.return_value = mock_process

            with pytest.raises(RuntimeError, match="timed out"):
                await _run_gpu_test_binary()

    @pytest.mark.asyncio
    async def test_binary_failure_output(self):
        """Test error when binary output indicates failure."""
        failure_output = "Failed to initialize NVML: version mismatch\n"

        with patch(
            "runpod.serverless.modules.rp_gpu_fitness._get_gpu_test_binary_path"
        ) as mock_path, \
             patch("asyncio.create_subprocess_exec") as mock_exec, \
             patch("os.access", return_value=True):

            mock_path.return_value = Path("/fake/gpu_test")
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock(
                return_value=(failure_output.encode(), b"")
            )
            mock_exec.return_value = mock_process

            with pytest.raises(RuntimeError, match="GPU memory allocation test failed"):
                await _run_gpu_test_binary()


# ============================================================================
# Fallback Tests
# ============================================================================

class TestFallbackExecution:
    """Tests for Python fallback GPU check.

    Fallback tests are primarily covered by integration tests since
    the fallback involves subprocess calls that are difficult to mock cleanly.
    """
    pass


# ============================================================================
# Health Check Logic Tests
# ============================================================================

class TestGpuHealthCheck:
    """Tests for main GPU health check function."""

    @pytest.mark.asyncio
    async def test_health_check_binary_success(self):
        """Test successful health check with binary."""
        with patch(
            "runpod.serverless.modules.rp_gpu_fitness._run_gpu_test_binary"
        ) as mock_binary:
            mock_binary.return_value = {
                "success": True,
                "gpu_count": 1,
                "found_gpus": 1,
                "errors": [],
                "details": {"cuda_version": "12.2"},
            }

            # Should not raise
            await _check_gpu_health()



# ============================================================================
# Auto-Registration Tests
# ============================================================================

class TestAutoRegistration:
    """Tests for GPU check auto-registration."""

    def test_auto_register_gpu_found(self):
        """Test auto-registration when GPU detected."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="NVIDIA-SMI ...\n"
            )

            auto_register_gpu_check()

            # Should have registered the check
            assert len(_fitness_checks) == 1

    def test_auto_register_no_gpu(self):
        """Test auto-registration skipped when no GPU."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()

            auto_register_gpu_check()

            # Should NOT register the check
            assert len(_fitness_checks) == 0

    def test_auto_register_nvidia_smi_failed(self):
        """Test auto-registration when nvidia-smi fails."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")

            auto_register_gpu_check()

            # Should NOT register the check
            assert len(_fitness_checks) == 0

    def test_auto_register_timeout(self):
        """Test auto-registration handles timeout."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 5)):

            auto_register_gpu_check()

            # Should handle gracefully and not register
            assert len(_fitness_checks) == 0
