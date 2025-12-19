"""
System resource fitness checks for worker startup validation.

Provides comprehensive checks for:
- Memory availability
- Disk space
- Network connectivity
- CUDA library versions
- GPU compute benchmark

Auto-registers when worker starts, ensuring system readiness before accepting jobs.
"""

import asyncio
import os
import shutil
import subprocess
import time
from typing import Any, Dict, Optional

from .rp_fitness import register_fitness_check
from .rp_logger import RunPodLogger
from ..utils.rp_cuda import is_available as gpu_available

log = RunPodLogger()

# Configuration via environment variables
MIN_MEMORY_GB = float(os.environ.get("RUNPOD_MIN_MEMORY_GB", "4.0"))
MIN_DISK_PERCENT = float(os.environ.get("RUNPOD_MIN_DISK_PERCENT", "10.0"))
MIN_CUDA_VERSION = os.environ.get("RUNPOD_MIN_CUDA_VERSION", "11.8")
NETWORK_CHECK_TIMEOUT = int(os.environ.get("RUNPOD_NETWORK_CHECK_TIMEOUT", "5"))
GPU_BENCHMARK_TIMEOUT = int(os.environ.get("RUNPOD_GPU_BENCHMARK_TIMEOUT", "2"))


def _parse_version(version_string: str) -> tuple:
    """
    Parse version string to tuple for comparison.

    Args:
        version_string: Version string like "12.2" or "CUDA Version 12.2"

    Returns:
        Tuple of ints like (12, 2) for comparison
    """
    import re

    # Extract numeric version
    match = re.search(r"(\d+)\.(\d+)", version_string)
    if match:
        return (int(match.group(1)), int(match.group(2)))
    return (0, 0)


def _get_memory_info() -> Dict[str, float]:
    """
    Get system memory information.

    Returns:
        Dict with total_gb, available_gb, used_percent

    Raises:
        RuntimeError: If memory check fails
    """
    try:
        import psutil

        mem = psutil.virtual_memory()
        total_gb = mem.total / (1024**3)
        available_gb = mem.available / (1024**3)
        used_percent = mem.percent

        return {
            "total_gb": total_gb,
            "available_gb": available_gb,
            "used_percent": used_percent,
        }
    except ImportError:
        # Fallback: parse /proc/meminfo
        try:
            with open("/proc/meminfo") as f:
                meminfo = {}
                for line in f:
                    key, value = line.split(":", 1)
                    meminfo[key.strip()] = int(value.split()[0]) / (1024**2)

                total_gb = meminfo.get("MemTotal", 0) / 1024
                available_gb = meminfo.get("MemAvailable", 0) / 1024
                used_percent = 100 * (1 - available_gb / total_gb) if total_gb > 0 else 0

                return {
                    "total_gb": total_gb,
                    "available_gb": available_gb,
                    "used_percent": used_percent,
                }
        except Exception as e:
            raise RuntimeError(f"Failed to read memory info: {e}")


def _check_memory_availability() -> None:
    """
    Check system memory availability.

    Raises:
        RuntimeError: If insufficient memory available
    """
    mem_info = _get_memory_info()
    available_gb = mem_info["available_gb"]
    total_gb = mem_info["total_gb"]

    if available_gb < MIN_MEMORY_GB:
        raise RuntimeError(
            f"Insufficient memory: {available_gb:.2f}GB available, "
            f"{MIN_MEMORY_GB}GB required"
        )

    log.info(
        f"Memory check passed: {available_gb:.2f}GB available "
        f"(of {total_gb:.2f}GB total)"
    )


def _check_disk_space() -> None:
    """
    Check disk space availability on root filesystem.

    In containers, root (/) is typically the only filesystem.
    Requires free space to be at least MIN_DISK_PERCENT% of total disk size.

    Raises:
        RuntimeError: If insufficient disk space
    """
    try:
        usage = shutil.disk_usage("/")
        total_gb = usage.total / (1024**3)
        free_gb = usage.free / (1024**3)
        free_percent = 100 * (free_gb / total_gb)

        # Check if free space is below the required percentage
        if free_percent < MIN_DISK_PERCENT:
            raise RuntimeError(
                f"Insufficient disk space: {free_gb:.2f}GB free "
                f"({free_percent:.1f}%), {MIN_DISK_PERCENT}% required"
            )

        log.info(
            f"Disk space check passed: {free_gb:.2f}GB free "
            f"({free_percent:.1f}% available)"
        )
    except FileNotFoundError:
        raise RuntimeError("Could not check disk space: / filesystem not found")


async def _check_network_connectivity() -> None:
    """
    Check basic network connectivity to 8.8.8.8:53.

    Raises:
        RuntimeError: If network connectivity fails
    """
    host = "8.8.8.8"
    port = 53

    try:
        start_time = time.time()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=NETWORK_CHECK_TIMEOUT
        )
        elapsed_ms = (time.time() - start_time) * 1000
        writer.close()
        await writer.wait_closed()

        log.info(f"Network connectivity passed: Connected to {host} ({elapsed_ms:.0f}ms)")
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"Network connectivity failed: Timeout connecting to {host}:{port} "
            f"({NETWORK_CHECK_TIMEOUT}s)"
        )
    except ConnectionRefusedError:
        raise RuntimeError(f"Network connectivity failed: Connection refused to {host}:{port}")
    except Exception as e:
        raise RuntimeError(f"Network connectivity check failed: {e}")


async def _get_cuda_version() -> Optional[str]:
    """
    Get CUDA version from system.

    Returns:
        Version string like "12.2" or None if not available

    Raises:
        RuntimeError: If CUDA check fails critically
    """
    # Try nvcc first
    try:
        result = subprocess.run(
            ["nvcc", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output: "nvcc: NVIDIA (R) Cuda compiler driver\n..."
            # Look for version pattern
            for line in result.stdout.split("\n"):
                if "release" in line.lower() or "version" in line.lower():
                    return line.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
        log.debug(f"nvcc not available: {e}")

    # Fallback: try nvidia-smi and parse CUDA version from output
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Parse CUDA version from header: "CUDA Version: 12.7"
            for line in result.stdout.split('\n'):
                if 'CUDA Version:' in line:
                    # Extract version after "CUDA Version:"
                    parts = line.split('CUDA Version:')
                    if len(parts) > 1:
                        # Get just the version number (e.g., "12.7")
                        cuda_version = parts[1].strip().split()[0]
                        return f"CUDA Version: {cuda_version}"
            log.debug("nvidia-smi output found but couldn't parse CUDA version")
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception) as e:
        log.debug(f"nvidia-smi not available: {e}")

    return None


async def _check_cuda_versions() -> None:
    """
    Check CUDA library versions meet minimum requirements.

    Raises:
        RuntimeError: If CUDA version is below minimum
    """
    cuda_version_str = await _get_cuda_version()

    if not cuda_version_str:
        log.warn("Could not determine CUDA version, skipping check")
        return

    # Parse version
    cuda_version = _parse_version(cuda_version_str)
    min_version = _parse_version(MIN_CUDA_VERSION)

    if cuda_version < min_version:
        raise RuntimeError(
            f"CUDA version too old: {cuda_version[0]}.{cuda_version[1]} found, "
            f"{min_version[0]}.{min_version[1]} required"
        )

    log.info(
        f"CUDA version check passed: {cuda_version[0]}.{cuda_version[1]} "
        f"(minimum: {min_version[0]}.{min_version[1]})"
    )


async def _check_cuda_initialization() -> None:
    """
    Verify CUDA can be initialized and devices are accessible.

    Tests actual device initialization, memory access, and device properties.
    This catches issues where CUDA appears available but fails at runtime.
    Skips silently on CPU-only workers.

    Raises:
        RuntimeError: If CUDA initialization or device access fails
    """
    # Skip on CPU-only workers
    if not gpu_available():
        log.debug("No GPU detected, skipping CUDA initialization check")
        return

    # Try PyTorch first (most common)
    try:
        import torch

        if not torch.cuda.is_available():
            log.debug("CUDA not available in PyTorch, skipping initialization check")
            return

        # Reset CUDA state to ensure clean initialization
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()

        # Verify device count
        device_count = torch.cuda.device_count()
        if device_count == 0:
            raise RuntimeError("No CUDA devices available despite cuda.is_available() being True")

        # Test each device
        for i in range(device_count):
            try:
                # Get device properties
                props = torch.cuda.get_device_properties(i)
                if props.total_memory == 0:
                    raise RuntimeError(f"GPU {i} reports zero memory")

                # Try allocating a small tensor on the device
                _ = torch.zeros(1024, device=f"cuda:{i}")
                torch.cuda.synchronize()

            except Exception as e:
                raise RuntimeError(f"Failed to initialize GPU {i}: {e}")

        log.info(f"CUDA initialization passed: {device_count} device(s) initialized successfully")
        return

    except ImportError:
        log.debug("PyTorch not available, trying CuPy...")
    except Exception as e:
        raise RuntimeError(f"CUDA initialization failed: {e}")

    # Fallback: try CuPy
    try:
        import cupy as cp

        # Reset CuPy state
        cp.cuda.Device().synchronize()

        # Verify devices
        device_count = cp.cuda.runtime.getDeviceCount()
        if device_count == 0:
            raise RuntimeError("No CUDA devices available via CuPy")

        # Test each device
        for i in range(device_count):
            try:
                cp.cuda.Device(i).use()
                # Try allocating memory
                _ = cp.zeros(1024)
                cp.cuda.Device().synchronize()
            except Exception as e:
                raise RuntimeError(f"Failed to initialize GPU {i} with CuPy: {e}")

        log.info(f"CUDA initialization passed: {device_count} device(s) initialized successfully")
        return

    except ImportError:
        log.debug("CuPy not available, skipping CUDA initialization check")
    except Exception as e:
        raise RuntimeError(f"CUDA initialization check failed: {e}")


async def _check_gpu_compute_benchmark() -> None:
    """
    Quick GPU compute benchmark using matrix multiplication.

    Tests basic tensor operations to ensure GPU is functional and responsive.
    Skips silently on CPU-only workers.

    Raises:
        RuntimeError: If GPU compute fails or is too slow
    """
    # Skip on CPU-only workers
    if not gpu_available():
        log.debug("No GPU detected, skipping GPU compute benchmark")
        return

    # Try PyTorch first
    try:
        import torch

        if not torch.cuda.is_available():
            log.debug("CUDA not available in PyTorch, skipping benchmark")
            return

        # Create small matrix on GPU
        size = 1024
        start_time = time.time()

        # Do computation
        A = torch.randn(size, size, device="cuda")
        B = torch.randn(size, size, device="cuda")
        C = torch.matmul(A, B)
        torch.cuda.synchronize()  # Wait for GPU to finish

        elapsed_ms = (time.time() - start_time) * 1000

        if elapsed_ms > 100:
            raise RuntimeError(
                f"GPU compute too slow: Matrix multiply took {elapsed_ms:.0f}ms "
                f"(max: 100ms)"
            )

        log.info(f"GPU compute benchmark passed: Matrix multiply completed in {elapsed_ms:.0f}ms")
        return

    except ImportError:
        log.debug("PyTorch not available, trying CuPy...")
    except Exception as e:
        log.warn(f"PyTorch GPU benchmark failed: {e}")

    # Fallback: try CuPy
    try:
        import cupy as cp

        size = 1024
        start_time = time.time()

        A = cp.random.randn(size, size)
        B = cp.random.randn(size, size)
        C = cp.matmul(A, B)
        cp.cuda.Device().synchronize()

        elapsed_ms = (time.time() - start_time) * 1000

        if elapsed_ms > 100:
            raise RuntimeError(
                f"GPU compute too slow: Matrix multiply took {elapsed_ms:.0f}ms "
                f"(max: 100ms)"
            )

        log.info(f"GPU compute benchmark passed: Matrix multiply completed in {elapsed_ms:.0f}ms")
        return

    except ImportError:
        log.debug("CuPy not available, skipping GPU benchmark")
    except Exception as e:
        log.warn(f"CuPy GPU benchmark failed: {e}")

    # If we get here, neither library is available
    log.debug("PyTorch/CuPy not available for GPU benchmark, relying on gpu_test binary")


def auto_register_system_checks() -> None:
    """
    Auto-register system resource fitness checks.

    Registers memory, disk, and network checks for all workers.
    Registers CUDA version, initialization, and GPU benchmark checks only if GPU is detected.
    """
    log.debug("Registering system resource fitness checks")

    # Always register these checks
    @register_fitness_check
    def _memory_check() -> None:
        """System memory availability check."""
        _check_memory_availability()

    @register_fitness_check
    def _disk_check() -> None:
        """System disk space check."""
        _check_disk_space()

    @register_fitness_check
    async def _network_check() -> None:
        """Network connectivity check."""
        await _check_network_connectivity()

    # Only register GPU checks if GPU is detected
    if gpu_available():
        log.debug("GPU detected, registering GPU-specific fitness checks")

        @register_fitness_check
        async def _cuda_version_check() -> None:
            """CUDA version check."""
            await _check_cuda_versions()

        @register_fitness_check
        async def _cuda_init_check() -> None:
            """CUDA device initialization check."""
            await _check_cuda_initialization()

        @register_fitness_check
        async def _benchmark_check() -> None:
            """GPU compute benchmark check."""
            await _check_gpu_compute_benchmark()
    else:
        log.debug("No GPU detected, skipping GPU-specific fitness checks")
