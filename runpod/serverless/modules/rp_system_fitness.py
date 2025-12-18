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
MIN_DISK_GB = float(os.environ.get("RUNPOD_MIN_DISK_GB", "10.0"))
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
    Check disk space availability on root and /tmp.

    Raises:
        RuntimeError: If insufficient disk space
    """
    paths_to_check = ["/", "/tmp"]

    for path in paths_to_check:
        try:
            usage = shutil.disk_usage(path)
            total_gb = usage.total / (1024**3)
            free_gb = usage.free / (1024**3)
            used_percent = 100 * (1 - free_gb / total_gb)

            if free_gb < MIN_DISK_GB:
                raise RuntimeError(
                    f"Insufficient disk space on {path}: {free_gb:.2f}GB free, "
                    f"{MIN_DISK_GB}GB required"
                )

            log.debug(
                f"Disk space check passed on {path}: {free_gb:.2f}GB free "
                f"({used_percent:.1f}% used)"
            )
        except FileNotFoundError:
            # /tmp may not exist on some systems
            if path == "/":
                # Root always exists
                raise
            # Skip /tmp if it doesn't exist
            log.debug(f"Path {path} not found, skipping disk check")


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

    # Fallback: try nvidia-smi
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
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
    Registers CUDA and GPU benchmark checks only if GPU is detected.
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
        async def _cuda_check() -> None:
            """CUDA version check."""
            await _check_cuda_versions()

        @register_fitness_check
        async def _benchmark_check() -> None:
            """GPU compute benchmark check."""
            await _check_gpu_compute_benchmark()
    else:
        log.debug("No GPU detected, skipping GPU-specific fitness checks")
