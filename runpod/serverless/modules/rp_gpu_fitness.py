"""
GPU fitness check system for worker startup validation.

Provides comprehensive GPU health checking using:
1. Native CUDA binary (gpu_test) for memory allocation testing
2. Python fallback using nvidia-smi if binary unavailable

Auto-registers when GPUs are detected, skips silently on CPU-only workers.
"""

import asyncio
import inspect
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from runpod._binary_helpers import get_binary_path
from .rp_fitness import register_fitness_check
from .rp_logger import RunPodLogger

log = RunPodLogger()

# Configuration via environment variables
TIMEOUT_SECONDS = int(os.environ.get("RUNPOD_GPU_TEST_TIMEOUT", "30"))


def _get_gpu_test_binary_path() -> Optional[Path]:
    """
    Locate gpu_test binary in package.

    Returns:
        Path to binary if found, None otherwise
    """
    return get_binary_path("gpu_test")


def _parse_gpu_test_output(output: str) -> Dict[str, Any]:
    """
    Parse gpu_test binary output and detect success/failure.

    Looks for:
    - "GPU X memory allocation test passed." for success
    - Error patterns: "Failed", "error", "cannot" for failures
    - GPU count from "Found X GPUs:" line

    Args:
        output: Stdout from gpu_test binary

    Returns:
        Dict with keys:
        - success: bool - True if all GPUs passed tests
        - gpu_count: int - Number of GPUs that passed tests
        - found_gpus: int - Total GPUs found
        - errors: List[str] - Error messages from output
        - details: Dict - CUDA version, kernel version, etc
    """
    lines = output.strip().split("\n")

    result = {
        "success": False,
        "gpu_count": 0,
        "found_gpus": 0,
        "errors": [],
        "details": {},
    }

    passed_count = 0
    found_gpus = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Extract metadata
        if line.startswith("CUDA Driver Version:"):
            result["details"]["cuda_version"] = line.split(":", 1)[1].strip()
        elif line.startswith("Linux Kernel Version:"):
            result["details"]["kernel"] = line.split(":", 1)[1].strip()
        elif line.startswith("Found") and "GPUs" in line:
            # "Found 2 GPUs:"
            try:
                found_gpus = int(line.split()[1])
                result["found_gpus"] = found_gpus
            except (IndexError, ValueError):
                pass

        # Check for success
        if "memory allocation test passed" in line.lower():
            passed_count += 1

        # Check for errors
        if any(
            err in line.lower() for err in ["failed", "error", "cannot", "unable"]
        ):
            result["errors"].append(line)

    result["gpu_count"] = passed_count
    result["success"] = (
        passed_count > 0 and passed_count == found_gpus and len(result["errors"]) == 0
    )

    return result


async def _run_gpu_test_binary() -> Dict[str, Any]:
    """
    Execute gpu_test binary and parse output.

    Returns:
        Parsed result dict from _parse_gpu_test_output

    Raises:
        RuntimeError: If binary execution fails or GPUs unhealthy
    """
    binary_path = _get_gpu_test_binary_path()

    if not binary_path:
        raise FileNotFoundError("gpu_test binary not found in package")

    if not os.access(binary_path, os.X_OK):
        raise PermissionError(f"gpu_test binary not executable: {binary_path}")

    log.debug(f"Running gpu_test binary: {binary_path}")

    try:
        # Run binary with timeout
        process = await asyncio.create_subprocess_exec(
            str(binary_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=TIMEOUT_SECONDS
        )

        output = stdout.decode("utf-8", errors="replace")
        error_output = stderr.decode("utf-8", errors="replace")

        log.debug(f"gpu_test output:\n{output}")

        if error_output:
            log.debug(f"gpu_test stderr:\n{error_output}")

        # Parse output
        result = _parse_gpu_test_output(output)

        # Check for success
        if not result["success"]:
            error_msg = "GPU memory allocation test failed"
            if result["errors"]:
                error_msg += f": {'; '.join(result['errors'][:3])}"  # Limit to 3 errors
            raise RuntimeError(error_msg)

        log.info(
            f"GPU binary test passed: {result['gpu_count']} GPU(s) healthy "
            f"(CUDA {result['details'].get('cuda_version', 'unknown')})"
        )

        return result

    except asyncio.TimeoutError:
        raise RuntimeError(
            f"GPU test binary timed out after {TIMEOUT_SECONDS}s"
        ) from None
    except FileNotFoundError as exc:
        raise exc
    except PermissionError as exc:
        raise exc
    except Exception as exc:
        raise RuntimeError(f"GPU test binary execution failed: {exc}") from exc


def _run_gpu_test_fallback() -> None:
    """
    Python fallback for GPU testing using nvidia-smi.

    Less comprehensive than binary (doesn't test memory allocation) but validates
    basic GPU availability.

    Raises:
        RuntimeError: If GPUs not available or unhealthy
    """
    log.debug("Running Python GPU fallback check")

    try:
        # Use existing rp_cuda utility
        from ..utils.rp_cuda import is_available

        if not is_available():
            raise RuntimeError(
                "GPU not available (nvidia-smi check failed). "
                "This is a fallback check - consider installing gpu_test binary."
            )

        # Additional check: Count GPUs
        result = subprocess.run(
            ["nvidia-smi", "--list-gpus"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(f"nvidia-smi --list-gpus failed: {result.stderr}")

        gpu_lines = [l for l in result.stdout.split("\n") if l.strip()]
        gpu_count = len(gpu_lines)

        if gpu_count == 0:
            raise RuntimeError("No GPUs detected by nvidia-smi")

        log.info(
            f"GPU fallback check passed: {gpu_count} GPU(s) detected "
            "(Note: Memory allocation NOT tested)"
        )

    except FileNotFoundError:
        raise RuntimeError("nvidia-smi not found. Cannot validate GPU availability.") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("nvidia-smi timed out") from None
    except Exception as exc:
        raise exc


async def _check_gpu_health() -> None:
    """
    Comprehensive GPU health check (internal implementation).

    Execution strategy:
    1. Try binary test if available
    2. Fall back to Python check if binary fails/missing
    3. Raise RuntimeError if all methods fail

    Raises:
        RuntimeError: If GPU health check fails
    """
    binary_attempted = False
    binary_error = None

    # Try binary first
    try:
        await _run_gpu_test_binary()
        return  # Success!
    except FileNotFoundError as exc:
        log.debug(f"GPU binary not found: {exc}")
        binary_error = exc
    except PermissionError as exc:
        log.debug(f"GPU binary not executable: {exc}")
        binary_error = exc
    except Exception as exc:
        log.warn(f"GPU binary check failed: {exc}")
        binary_attempted = True
        binary_error = exc

    # Fall back to Python
    log.debug("Attempting Python GPU fallback check")
    try:
        _run_gpu_test_fallback()
        return  # Success!
    except Exception as fallback_exc:
        # Both failed - raise composite error
        if binary_attempted:
            raise RuntimeError(
                f"GPU health check failed. "
                f"Binary test: {binary_error}. "
                f"Fallback test: {fallback_exc}"
            ) from fallback_exc
        else:
            raise RuntimeError(
                f"GPU health check failed (binary disabled/missing, "
                f"fallback failed): {fallback_exc}"
            ) from fallback_exc


def auto_register_gpu_check() -> None:
    """
    Auto-register GPU fitness check if GPUs are detected.

    This function is called during rp_fitness module initialization.
    It detects GPU presence via nvidia-smi and registers the check if found.
    On CPU-only workers, the check is skipped silently.

    The check cannot be disabled when GPUs are present - this is a required
    health check for GPU workers.

    Environment variables:
    - RUNPOD_SKIP_GPU_CHECK: Set to "true" to skip auto-registration (for testing)
    """
    # Allow skipping during tests
    if os.environ.get("RUNPOD_SKIP_GPU_CHECK", "").lower() == "true":
        log.debug("GPU fitness check auto-registration disabled via environment")
        return

    # Quick GPU detection
    has_gpu = False
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        has_gpu = result.returncode == 0 and "NVIDIA-SMI" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        has_gpu = False
    except Exception:
        has_gpu = False

    if has_gpu:
        log.debug("GPU detected, registering automatic GPU fitness check")

        @register_fitness_check
        async def _gpu_health_check():
            """Automatic GPU memory allocation health check."""
            await _check_gpu_health()
    else:
        log.debug("No GPU detected, skipping GPU fitness check registration")
