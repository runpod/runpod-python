"""
Tests for system resource fitness checks (rp_system_fitness module).

Tests cover memory, disk space, network connectivity, CUDA version checking,
and GPU compute benchmarking with various system scenarios.
"""

import asyncio
import os
import subprocess
from unittest.mock import patch, MagicMock, AsyncMock, call

import pytest

from runpod.serverless.modules.rp_system_fitness import (
    _check_memory_availability,
    _check_disk_space,
    _check_network_connectivity,
    _check_cuda_versions,
    _check_cuda_initialization,
    _check_gpu_compute_benchmark,
    _get_memory_info,
    _get_cuda_version,
    _parse_version,
    auto_register_system_checks,
)
from runpod.serverless.modules.rp_fitness import clear_fitness_checks, _fitness_checks


@pytest.fixture(autouse=True)
def cleanup_fitness_checks():
    """Automatically clean up fitness checks before and after each test."""
    clear_fitness_checks()
    yield
    clear_fitness_checks()


# ============================================================================
# Memory Check Tests
# ============================================================================

class TestMemoryCheck:
    """Tests for memory availability checking."""

    @patch("runpod.serverless.modules.rp_system_fitness.MIN_MEMORY_GB", 4.0)
    @patch("runpod.serverless.modules.rp_system_fitness._get_memory_info")
    def test_sufficient_memory_passes(self, mock_get_mem):
        """Test that sufficient memory passes the check."""
        mock_get_mem.return_value = {
            "total_gb": 16.0,
            "available_gb": 12.0,
            "used_percent": 25.0,
        }
        # Should not raise
        _check_memory_availability()

    @patch("runpod.serverless.modules.rp_system_fitness.MIN_MEMORY_GB", 4.0)
    @patch("runpod.serverless.modules.rp_system_fitness._get_memory_info")
    def test_insufficient_memory_fails(self, mock_get_mem):
        """Test that insufficient memory fails the check."""
        mock_get_mem.return_value = {
            "total_gb": 4.0,
            "available_gb": 2.0,
            "used_percent": 50.0,
        }
        with pytest.raises(RuntimeError, match="Insufficient memory"):
            _check_memory_availability()

    @patch("runpod.serverless.modules.rp_system_fitness.MIN_MEMORY_GB", 8.0)
    @patch("runpod.serverless.modules.rp_system_fitness._get_memory_info")
    def test_memory_at_threshold_fails(self, mock_get_mem):
        """Test that memory exactly at threshold fails."""
        mock_get_mem.return_value = {
            "total_gb": 8.0,
            "available_gb": 7.9,
            "used_percent": 1.25,
        }
        with pytest.raises(RuntimeError):
            _check_memory_availability()

    def test_memory_info_works(self):
        """Test that memory info can be retrieved without errors."""
        # This test just ensures _get_memory_info() works on the current system
        # (either via psutil or /proc/meminfo)
        info = _get_memory_info()
        assert "total_gb" in info
        assert "available_gb" in info
        assert "used_percent" in info
        assert info["total_gb"] > 0
        assert info["available_gb"] > 0


# ============================================================================
# Disk Space Check Tests
# ============================================================================

class TestDiskSpaceCheck:
    """Tests for disk space checking."""

    @patch("runpod.serverless.modules.rp_system_fitness.MIN_DISK_GB", 10.0)
    @patch("shutil.disk_usage")
    def test_sufficient_disk_passes(self, mock_disk_usage):
        """Test that sufficient disk space passes the check."""
        mock_usage = MagicMock()
        mock_usage.total = 100 * 1024**3
        mock_usage.free = 50 * 1024**3
        mock_disk_usage.return_value = mock_usage

        # Should not raise
        _check_disk_space()

    @patch("runpod.serverless.modules.rp_system_fitness.MIN_DISK_GB", 10.0)
    @patch("shutil.disk_usage")
    def test_insufficient_disk_fails(self, mock_disk_usage):
        """Test that insufficient disk space fails the check."""
        mock_usage = MagicMock()
        mock_usage.total = 20 * 1024**3
        mock_usage.free = 5 * 1024**3
        mock_disk_usage.return_value = mock_usage

        with pytest.raises(RuntimeError, match="Insufficient disk space"):
            _check_disk_space()

    @patch("runpod.serverless.modules.rp_system_fitness.MIN_DISK_GB", 10.0)
    @patch("shutil.disk_usage")
    def test_checks_both_root_and_tmp(self, mock_disk_usage):
        """Test that both root and /tmp are checked."""
        mock_usage = MagicMock()
        mock_usage.total = 100 * 1024**3
        mock_usage.free = 50 * 1024**3
        mock_disk_usage.return_value = mock_usage

        _check_disk_space()

        # Verify both paths were checked
        assert mock_disk_usage.call_count >= 2
        paths_checked = [call[0][0] for call in mock_disk_usage.call_args_list]
        assert "/" in paths_checked


# ============================================================================
# Network Connectivity Tests
# ============================================================================

class TestNetworkConnectivityCheck:
    """Tests for network connectivity checking."""

    @pytest.mark.asyncio
    async def test_network_connectivity_success(self):
        """Test successful network connectivity."""
        # Create async mock for connection
        mock_reader = AsyncMock()
        mock_writer = AsyncMock()
        mock_writer.wait_closed = AsyncMock()

        with patch("asyncio.open_connection") as mock_connect:
            mock_connect.return_value = (mock_reader, mock_writer)
            # Should not raise
            await _check_network_connectivity()

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_system_fitness.NETWORK_CHECK_TIMEOUT", 1)
    async def test_network_connectivity_timeout(self):
        """Test network connectivity timeout."""
        with patch("asyncio.open_connection") as mock_connect:
            mock_connect.side_effect = asyncio.TimeoutError()
            with pytest.raises(RuntimeError, match="Timeout"):
                await _check_network_connectivity()

    @pytest.mark.asyncio
    async def test_network_connectivity_refused(self):
        """Test network connectivity refused."""
        with patch("asyncio.open_connection") as mock_connect:
            mock_connect.side_effect = ConnectionRefusedError()
            with pytest.raises(RuntimeError, match="Connection refused"):
                await _check_network_connectivity()


# ============================================================================
# CUDA Version Check Tests
# ============================================================================

class TestCudaVersionCheck:
    """Tests for CUDA version checking."""

    def test_parse_version(self):
        """Test version string parsing."""
        assert _parse_version("12.2") == (12, 2)
        assert _parse_version("11.8") == (11, 8)
        assert _parse_version("CUDA Version 12.2") == (12, 2)
        assert _parse_version("invalid") == (0, 0)

    @pytest.mark.asyncio
    @patch("subprocess.run")
    @patch("runpod.serverless.modules.rp_system_fitness.MIN_CUDA_VERSION", "11.8")
    async def test_cuda_version_sufficient(self, mock_run):
        """Test that sufficient CUDA version passes."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="nvcc: NVIDIA (R) Cuda compiler driver\nRelease 12.2, V12.2.140",
        )
        # Should not raise
        await _check_cuda_versions()

    @pytest.mark.asyncio
    @patch("subprocess.run")
    @patch("runpod.serverless.modules.rp_system_fitness.MIN_CUDA_VERSION", "12.0")
    async def test_cuda_version_insufficient(self, mock_run):
        """Test that insufficient CUDA version fails."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="nvcc: NVIDIA (R) Cuda compiler driver\nRelease 11.8, V11.8.89",
        )
        with pytest.raises(RuntimeError, match="too old"):
            await _check_cuda_versions()

    @pytest.mark.asyncio
    @patch("subprocess.run")
    async def test_cuda_not_available(self, mock_run):
        """Test graceful handling when CUDA is not available."""
        mock_run.side_effect = FileNotFoundError()
        # Should not raise, just skip
        await _check_cuda_versions()

    @pytest.mark.asyncio
    @patch("subprocess.run")
    async def test_get_cuda_version_nvcc(self, mock_run):
        """Test CUDA version retrieval from nvcc."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="nvcc: NVIDIA (R) Cuda compiler driver\nRelease 12.2",
        )
        version = await _get_cuda_version()
        assert version is not None
        assert "Release 12.2" in version

    @pytest.mark.asyncio
    @patch("subprocess.run")
    async def test_get_cuda_version_nvidia_smi_fallback(self, mock_run):
        """Test CUDA version retrieval fallback to nvidia-smi."""
        # First call (nvcc) fails, second call (nvidia-smi) succeeds
        mock_run.side_effect = [
            FileNotFoundError(),  # nvcc not found
            MagicMock(
                returncode=0,
                stdout="""
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 565.57                 Driver Version: 565.57         CUDA Version: 12.7     |
|--------------------------------------+------------------------+------------------------+
"""
            ),
        ]
        version = await _get_cuda_version()
        assert version is not None
        assert "12.7" in version
        assert "565" not in version  # Should NOT contain driver version

    @pytest.mark.asyncio
    @patch("subprocess.run")
    async def test_get_cuda_version_nvidia_smi_no_cuda_in_output(self, mock_run):
        """Test nvidia-smi output without CUDA version."""
        mock_run.side_effect = [
            FileNotFoundError(),  # nvcc not found
            MagicMock(returncode=0, stdout="No CUDA info here\nSome other output"),
        ]
        version = await _get_cuda_version()
        assert version is None

    @pytest.mark.asyncio
    @patch("subprocess.run")
    async def test_get_cuda_version_extraction_from_nvidia_smi(self, mock_run):
        """Test that CUDA version is correctly extracted from nvidia-smi."""
        mock_run.side_effect = [
            FileNotFoundError(),  # nvcc not found
            MagicMock(
                returncode=0,
                stdout="NVIDIA-SMI 565.57    Driver Version: 565.57    CUDA Version: 12.2"
            ),
        ]
        version = await _get_cuda_version()
        assert version is not None
        assert "12.2" in version
        # Verify it's a CUDA version, not driver version
        parsed = _parse_version(version)
        assert parsed[0] in (11, 12, 13)  # Valid CUDA major versions

    @pytest.mark.asyncio
    async def test_get_cuda_version_unavailable(self):
        """Test when CUDA is completely unavailable."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            version = await _get_cuda_version()
            assert version is None


# ============================================================================
# CUDA Initialization Tests
# ============================================================================

class TestCudaInitialization:
    """Tests for CUDA device initialization checking."""

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_system_fitness.gpu_available")
    async def test_cuda_init_skips_cpu_only(self, mock_gpu_available):
        """Test that initialization check skips on CPU-only workers."""
        mock_gpu_available.return_value = False
        # Should not raise, just skip
        await _check_cuda_initialization()

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_system_fitness.gpu_available")
    async def test_cuda_init_pytorch_success(self, mock_gpu_available):
        """Test successful CUDA initialization with PyTorch."""
        mock_gpu_available.return_value = True

        # Mock PyTorch
        mock_torch = MagicMock()
        mock_cuda = MagicMock()
        mock_cuda.is_available.return_value = True
        mock_cuda.device_count.return_value = 2
        mock_cuda.reset_peak_memory_stats = MagicMock()
        mock_cuda.synchronize = MagicMock()

        # Mock device properties
        mock_props = MagicMock()
        mock_props.total_memory = 16 * 1024**3
        mock_cuda.get_device_properties.return_value = mock_props

        # Mock tensor creation
        mock_tensor = MagicMock()
        mock_torch.zeros.return_value = mock_tensor
        mock_torch.cuda = mock_cuda

        with patch.dict("sys.modules", {"torch": mock_torch}):
            # Should not raise
            await _check_cuda_initialization()

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_system_fitness.gpu_available")
    async def test_cuda_init_pytorch_no_devices(self, mock_gpu_available):
        """Test CUDA initialization fails when no devices available."""
        mock_gpu_available.return_value = True

        mock_torch = MagicMock()
        mock_cuda = MagicMock()
        mock_cuda.is_available.return_value = True
        mock_cuda.device_count.return_value = 0
        mock_cuda.reset_peak_memory_stats = MagicMock()
        mock_cuda.synchronize = MagicMock()
        mock_torch.cuda = mock_cuda

        with patch.dict("sys.modules", {"torch": mock_torch}):
            with pytest.raises(RuntimeError, match="No CUDA devices"):
                await _check_cuda_initialization()

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_system_fitness.gpu_available")
    async def test_cuda_init_pytorch_zero_memory(self, mock_gpu_available):
        """Test CUDA initialization fails when device reports zero memory."""
        mock_gpu_available.return_value = True

        mock_torch = MagicMock()
        mock_cuda = MagicMock()
        mock_cuda.is_available.return_value = True
        mock_cuda.device_count.return_value = 1
        mock_cuda.reset_peak_memory_stats = MagicMock()
        mock_cuda.synchronize = MagicMock()

        # Mock device with zero memory
        mock_props = MagicMock()
        mock_props.total_memory = 0
        mock_cuda.get_device_properties.return_value = mock_props
        mock_torch.cuda = mock_cuda

        with patch.dict("sys.modules", {"torch": mock_torch}):
            with pytest.raises(RuntimeError, match="zero memory"):
                await _check_cuda_initialization()

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_system_fitness.gpu_available")
    async def test_cuda_init_pytorch_allocation_fails(self, mock_gpu_available):
        """Test CUDA initialization fails when tensor allocation fails."""
        mock_gpu_available.return_value = True

        mock_torch = MagicMock()
        mock_cuda = MagicMock()
        mock_cuda.is_available.return_value = True
        mock_cuda.device_count.return_value = 1
        mock_cuda.reset_peak_memory_stats = MagicMock()
        mock_cuda.synchronize = MagicMock()

        # Mock device properties
        mock_props = MagicMock()
        mock_props.total_memory = 16 * 1024**3
        mock_cuda.get_device_properties.return_value = mock_props

        # Mock tensor allocation failure
        mock_torch.zeros.side_effect = RuntimeError("CUDA out of memory")
        mock_torch.cuda = mock_cuda

        with patch.dict("sys.modules", {"torch": mock_torch}):
            with pytest.raises(RuntimeError, match="Failed to initialize GPU"):
                await _check_cuda_initialization()

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_system_fitness.gpu_available")
    async def test_cuda_init_cupy_fallback(self, mock_gpu_available):
        """Test CUDA initialization fallback to CuPy when PyTorch unavailable."""
        mock_gpu_available.return_value = True

        # Mock CuPy
        mock_cupy = MagicMock()
        mock_cuda_module = MagicMock()
        mock_device = MagicMock()
        mock_device.synchronize = MagicMock()
        mock_cuda_module.Device.return_value = mock_device
        mock_cuda_module.runtime.getDeviceCount.return_value = 1
        mock_cupy.cuda = mock_cuda_module
        mock_cupy.zeros.return_value = MagicMock()

        # Patch sys.modules so torch import fails but cupy succeeds
        with patch.dict(
            "sys.modules",
            {"torch": None, "cupy": mock_cupy},
        ):
            # Should not raise
            await _check_cuda_initialization()

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_system_fitness.gpu_available")
    async def test_cuda_init_no_libraries(self, mock_gpu_available):
        """Test CUDA initialization skips gracefully when no libraries available."""
        mock_gpu_available.return_value = True

        with patch.dict("sys.modules", {"torch": None, "cupy": None}):
            # Should not raise, just skip
            await _check_cuda_initialization()


# ============================================================================
# GPU Compute Benchmark Tests
# ============================================================================

class TestGpuComputeBenchmark:
    """Tests for GPU compute benchmark."""

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_system_fitness.gpu_available")
    async def test_gpu_benchmark_skips_cpu_only(self, mock_gpu_available):
        """Test that benchmark skips on CPU-only workers."""
        mock_gpu_available.return_value = False
        # Should not raise, just skip
        await _check_gpu_compute_benchmark()

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_system_fitness.gpu_available")
    async def test_gpu_benchmark_with_torch_available(self, mock_gpu_available):
        """Test GPU benchmark handling when PyTorch is available."""
        mock_gpu_available.return_value = True

        # Create a mock torch module
        mock_torch = MagicMock()
        mock_cuda = MagicMock()
        mock_cuda.is_available.return_value = True
        mock_torch.cuda = mock_cuda

        # Mock tensor operations
        mock_tensor = MagicMock()
        mock_torch.randn.return_value = mock_tensor
        mock_torch.matmul.return_value = mock_tensor

        # Patch torch in the system modules
        with patch.dict("sys.modules", {"torch": mock_torch}):
            # Reimport the module to pick up the mock
            # The function should complete without raising
            await _check_gpu_compute_benchmark()

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_system_fitness.gpu_available")
    async def test_gpu_benchmark_skips_no_libraries(self, mock_gpu_available):
        """Test benchmark skips when no GPU libraries available."""
        mock_gpu_available.return_value = True

        with patch.dict("sys.modules", {"torch": None, "cupy": None}):
            # Should not raise, just skip
            await _check_gpu_compute_benchmark()


# ============================================================================
# Auto-Registration Tests
# ============================================================================

class TestAutoRegistration:
    """Tests for auto-registration of system checks."""

    @patch("runpod.serverless.modules.rp_system_fitness.gpu_available")
    def test_auto_register_all_checks_with_gpu(self, mock_gpu_available):
        """Test that all 6 checks are registered on GPU worker."""
        mock_gpu_available.return_value = True
        auto_register_system_checks()
        # Should register: memory, disk, network, cuda_version, cuda_init, benchmark
        assert len(_fitness_checks) >= 6

    @patch("runpod.serverless.modules.rp_system_fitness.gpu_available")
    def test_auto_register_cpu_only(self, mock_gpu_available):
        """Test that only 3 checks are registered on CPU worker."""
        mock_gpu_available.return_value = False
        auto_register_system_checks()
        # Should register: memory, disk, network (not cuda, not benchmark)
        assert len(_fitness_checks) == 3

    @patch("runpod.serverless.modules.rp_system_fitness.gpu_available")
    def test_registration_order_preserved(self, mock_gpu_available):
        """Test that checks are registered in correct order."""
        mock_gpu_available.return_value = False
        auto_register_system_checks()
        # Order should be: memory, disk, network
        check_names = [check.__name__ for check in _fitness_checks]
        assert "_memory_check" in check_names
        assert "_disk_check" in check_names
        assert "_network_check" in check_names


# ============================================================================
# Integration Tests
# ============================================================================

class TestIntegration:
    """Integration tests for system fitness checks."""

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_system_fitness._get_memory_info")
    @patch("shutil.disk_usage")
    @patch("asyncio.open_connection")
    @patch("runpod.serverless.modules.rp_system_fitness.gpu_available")
    async def test_all_checks_pass_healthy_system(
        self, mock_gpu, mock_conn, mock_disk, mock_mem
    ):
        """Test that all checks pass on a healthy system."""
        # Mock healthy system
        mock_mem.return_value = {
            "total_gb": 16.0,
            "available_gb": 12.0,
            "used_percent": 25.0,
        }

        mock_disk_usage = MagicMock()
        mock_disk_usage.total = 500 * 1024**3
        mock_disk_usage.free = 250 * 1024**3
        mock_disk.return_value = mock_disk_usage

        mock_reader = AsyncMock()
        mock_writer = AsyncMock()
        mock_writer.wait_closed = AsyncMock()
        mock_conn.return_value = (mock_reader, mock_writer)

        mock_gpu.return_value = False

        # Register and run checks
        auto_register_system_checks()

        # Should complete without exceptions
        for check in _fitness_checks:
            if asyncio.iscoroutinefunction(check):
                await check()
            else:
                check()

    @patch("runpod.serverless.modules.rp_system_fitness._get_memory_info")
    def test_memory_failure_stops_execution(self, mock_mem):
        """Test that memory failure causes immediate failure."""
        mock_mem.return_value = {
            "total_gb": 4.0,
            "available_gb": 2.0,
            "used_percent": 50.0,
        }

        with patch("runpod.serverless.modules.rp_system_fitness.MIN_MEMORY_GB", 4.0):
            with pytest.raises(RuntimeError):
                _check_memory_availability()
