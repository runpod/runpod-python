"""
Integration tests for GPU fitness check with mock binaries.

Tests the fitness check integration with actual subprocess execution
(using mock binaries) and fitness system interaction.
"""

import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch

from runpod.serverless.modules.rp_fitness import (
    register_fitness_check,
    run_fitness_checks,
    clear_fitness_checks,
)
from runpod.serverless.modules.rp_gpu_fitness import _check_gpu_health


@pytest.fixture(autouse=True)
def cleanup_checks():
    """Clean fitness checks before and after each test."""
    clear_fitness_checks()
    yield
    clear_fitness_checks()


@pytest.fixture
def mock_gpu_test_binary():
    """Create a temporary mock gpu_test binary that outputs success."""
    with tempfile.NamedTemporaryFile(mode="w", suffix="_gpu_test", delete=False) as f:
        f.write("""#!/bin/bash
cat <<'EOF'
Linux Kernel Version: 5.15.0
CUDA Driver Version: 12.2
Found 1 GPUs:
GPU 0: NVIDIA A100 (UUID: GPU-xxx)
GPU 0 memory allocation test passed.
EOF
exit 0
""")
        binary_path = f.name

    os.chmod(binary_path, 0o755)
    yield Path(binary_path)

    # Cleanup
    try:
        os.unlink(binary_path)
    except OSError:
        pass


@pytest.fixture
def mock_gpu_test_binary_failure():
    """Create a temporary mock gpu_test binary that outputs failure."""
    with tempfile.NamedTemporaryFile(mode="w", suffix="_gpu_test_fail", delete=False) as f:
        f.write("""#!/bin/bash
cat <<'EOF'
Failed to initialize NVML: Driver/library version mismatch
EOF
exit 0
""")
        binary_path = f.name

    os.chmod(binary_path, 0o755)
    yield Path(binary_path)

    # Cleanup
    try:
        os.unlink(binary_path)
    except OSError:
        pass


@pytest.fixture
def mock_gpu_test_binary_multi_gpu():
    """Create a temporary mock gpu_test binary with multiple GPUs."""
    with tempfile.NamedTemporaryFile(mode="w", suffix="_gpu_test_multi", delete=False) as f:
        f.write("""#!/bin/bash
cat <<'EOF'
Linux Kernel Version: 5.15.0
CUDA Driver Version: 12.2
Found 2 GPUs:
GPU 0: NVIDIA A100 (UUID: GPU-xxx)
GPU 0 memory allocation test passed.
GPU 1: NVIDIA A100 (UUID: GPU-yyy)
GPU 1 memory allocation test passed.
EOF
exit 0
""")
        binary_path = f.name

    os.chmod(binary_path, 0o755)
    yield Path(binary_path)

    # Cleanup
    try:
        os.unlink(binary_path)
    except OSError:
        pass


# ============================================================================
# Integration Tests with Mock Binaries
# ============================================================================

class TestGpuFitnessIntegration:
    """Integration tests using actual subprocess with mock binaries."""

    @pytest.mark.asyncio
    async def test_fitness_check_with_success_binary(self, mock_gpu_test_binary):
        """Test fitness check with successful mock binary."""
        @register_fitness_check
        async def gpu_check():
            with patch(
                "runpod.serverless.modules.rp_gpu_fitness._get_gpu_test_binary_path"
            ) as mock_path:
                mock_path.return_value = mock_gpu_test_binary
                await _check_gpu_health()

        # Should pass without raising or exiting
        await run_fitness_checks()

    @pytest.mark.asyncio
    async def test_fitness_check_with_failure_binary(self, mock_gpu_test_binary_failure):
        """Test fitness check fails with broken binary output."""
        @register_fitness_check
        async def gpu_check():
            with patch(
                "runpod.serverless.modules.rp_gpu_fitness._get_gpu_test_binary_path"
            ) as mock_path, \
                 patch(
                "runpod.serverless.modules.rp_gpu_fitness._run_gpu_test_fallback"
            ) as mock_fallback:
                mock_path.return_value = mock_gpu_test_binary_failure
                mock_fallback.side_effect = RuntimeError("Fallback also failed")
                await _check_gpu_health()

        # Should fail with system exit
        with pytest.raises(SystemExit) as exc_info:
            await run_fitness_checks()

        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_fitness_check_with_multi_gpu(self, mock_gpu_test_binary_multi_gpu):
        """Test fitness check with multiple GPUs."""
        @register_fitness_check
        async def gpu_check():
            with patch(
                "runpod.serverless.modules.rp_gpu_fitness._get_gpu_test_binary_path"
            ) as mock_path:
                mock_path.return_value = mock_gpu_test_binary_multi_gpu
                await _check_gpu_health()

        # Should pass without raising or exiting
        await run_fitness_checks()

    @pytest.mark.asyncio
    async def test_fitness_check_fallback_on_binary_missing(self):
        """Test fallback when binary is missing."""
        @register_fitness_check
        async def gpu_check():
            with patch(
                "runpod.serverless.modules.rp_gpu_fitness._get_gpu_test_binary_path"
            ) as mock_path, \
                 patch(
                "runpod.serverless.modules.rp_gpu_fitness._run_gpu_test_fallback"
            ) as mock_fallback:
                mock_path.return_value = None
                mock_fallback.return_value = None
                await _check_gpu_health()

        # Should pass because fallback succeeds
        await run_fitness_checks()

    @pytest.mark.asyncio
    async def test_fitness_check_with_timeout(self, mock_gpu_test_binary):
        """Test fitness check handles timeout gracefully."""
        @register_fitness_check
        async def gpu_check():
            with patch(
                "runpod.serverless.modules.rp_gpu_fitness._get_gpu_test_binary_path"
            ) as mock_path, \
                 patch(
                "asyncio.wait_for",
                side_effect=TimeoutError()
            ) as mock_wait_for, \
                 patch(
                "runpod.serverless.modules.rp_gpu_fitness._run_gpu_test_fallback"
            ) as mock_fallback:
                mock_path.return_value = mock_gpu_test_binary
                mock_fallback.side_effect = RuntimeError("Fallback failed")
                await _check_gpu_health()

        # Should fail due to timeout + fallback failure
        with pytest.raises(SystemExit) as exc_info:
            await run_fitness_checks()

        assert exc_info.value.code == 1


# ============================================================================
# CPU Worker Scenario Tests
# ============================================================================

class TestCpuWorkerScenario:
    """Test GPU check behavior on CPU-only workers."""

    @pytest.mark.asyncio
    async def test_cpu_worker_with_no_gpu_fitness_check(self):
        """Test that no GPU check runs on CPU-only worker."""
        from runpod.serverless.modules.rp_gpu_fitness import auto_register_gpu_check

        with patch("subprocess.run") as mock_run:
            # Simulate nvidia-smi not available
            mock_run.side_effect = FileNotFoundError()

            auto_register_gpu_check()

            # Should not register any fitness checks
            from runpod.serverless.modules.rp_fitness import _fitness_checks
            assert len(_fitness_checks) == 0


# ============================================================================
# Multiple Check Execution Order Tests
# ============================================================================

class TestMultipleCheckExecution:
    """Test GPU check integration with other fitness checks."""

    @pytest.mark.asyncio
    async def test_gpu_check_runs_in_correct_order(self):
        """Test GPU check runs after registration order."""
        execution_order = []

        @register_fitness_check
        def check_one():
            execution_order.append(1)

        @register_fitness_check
        async def gpu_check():
            execution_order.append(2)
            with patch(
                "runpod.serverless.modules.rp_gpu_fitness._get_gpu_test_binary_path"
            ) as mock_path, \
                 patch("asyncio.create_subprocess_exec") as mock_exec, \
                 patch("os.access", return_value=True):
                mock_path.return_value = None  # Force fallback
                with patch(
                    "runpod.serverless.modules.rp_gpu_fitness._run_gpu_test_fallback"
                ):
                    await _check_gpu_health()

        @register_fitness_check
        def check_three():
            execution_order.append(3)

        await run_fitness_checks()

        assert execution_order == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_gpu_check_stops_execution_on_failure(self):
        """Test that GPU check failure stops other checks."""
        execution_order = []

        @register_fitness_check
        def check_one():
            execution_order.append(1)

        @register_fitness_check
        async def gpu_check():
            execution_order.append(2)
            with patch(
                "runpod.serverless.modules.rp_gpu_fitness._get_gpu_test_binary_path"
            ) as mock_path, \
                 patch(
                "runpod.serverless.modules.rp_gpu_fitness._run_gpu_test_fallback"
            ) as mock_fallback:
                mock_path.return_value = None
                mock_fallback.side_effect = RuntimeError("GPU failed")
                await _check_gpu_health()

        @register_fitness_check
        def check_three():
            execution_order.append(3)

        # Should exit at GPU check failure
        with pytest.raises(SystemExit) as exc_info:
            await run_fitness_checks()

        # check_three should NOT have run
        assert execution_order == [1, 2]
        assert exc_info.value.code == 1
