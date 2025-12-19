"""
Tests for the fitness check system (rp_fitness module).

Fitness checks are used to validate worker health at startup before handler
initialization. Only tests registration, execution, and error handling of
fitness checks. Does NOT test integration with worker startup.
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock

from runpod.serverless.modules.rp_fitness import (
    register_fitness_check,
    run_fitness_checks,
    clear_fitness_checks,
    _fitness_checks,
    _reset_registration_state,
)


@pytest.fixture(autouse=True)
def cleanup_fitness_checks(monkeypatch):
    """Automatically clean up fitness checks before and after each test."""
    # Disable auto-registration of system checks for isolated fitness check tests
    monkeypatch.setenv("RUNPOD_SKIP_AUTO_SYSTEM_CHECKS", "true")
    _reset_registration_state()
    clear_fitness_checks()
    yield
    _reset_registration_state()
    clear_fitness_checks()


# ============================================================================
# Registration Tests
# ============================================================================

class TestFitnessRegistration:
    """Tests for fitness check registration via decorator."""

    def test_register_sync_function(self):
        """Test registering a synchronous fitness check."""
        @register_fitness_check
        def check_sync():
            pass

        assert len(_fitness_checks) == 1
        assert _fitness_checks[0] == check_sync

    def test_register_async_function(self):
        """Test registering an asynchronous fitness check."""
        @register_fitness_check
        async def check_async():
            pass

        assert len(_fitness_checks) == 1
        assert _fitness_checks[0] == check_async

    def test_register_multiple_functions(self):
        """Test registering multiple fitness checks."""
        @register_fitness_check
        def check_one():
            pass

        @register_fitness_check
        def check_two():
            pass

        @register_fitness_check
        async def check_three():
            pass

        assert len(_fitness_checks) == 3
        assert _fitness_checks[0] == check_one
        assert _fitness_checks[1] == check_two
        assert _fitness_checks[2] == check_three

    def test_decorator_returns_original_function(self):
        """Test that decorator returns the original function unchanged."""
        def check():
            return "result"

        decorated = register_fitness_check(check)
        assert decorated is check
        assert decorated() == "result"

    def test_decorator_allows_stacking(self):
        """Test that multiple decorators can be stacked."""
        def dummy_decorator(func):
            return func

        @register_fitness_check
        @dummy_decorator
        def check():
            pass

        assert len(_fitness_checks) == 1
        assert _fitness_checks[0] == check

    def test_duplicate_registration(self):
        """Test that the same function can be registered multiple times."""
        def check():
            pass

        register_fitness_check(check)
        register_fitness_check(check)

        assert len(_fitness_checks) == 2
        assert _fitness_checks[0] == check
        assert _fitness_checks[1] == check


# ============================================================================
# Execution Tests - Success Cases
# ============================================================================

class TestFitnessExecutionSuccess:
    """Tests for successful fitness check execution."""


    @pytest.mark.asyncio
    async def test_empty_registry_no_op(self):
        """Test that empty registry results in no-op."""
        # Should not raise or exit
        await run_fitness_checks()

    @pytest.mark.asyncio
    async def test_single_sync_check_passes(self):
        """Test single synchronous check that passes."""
        check_called = False

        @register_fitness_check
        def check():
            nonlocal check_called
            check_called = True

        await run_fitness_checks()
        assert check_called

    @pytest.mark.asyncio
    async def test_single_async_check_passes(self):
        """Test single asynchronous check that passes."""
        check_called = False

        @register_fitness_check
        async def check():
            nonlocal check_called
            check_called = True

        await run_fitness_checks()
        assert check_called

    @pytest.mark.asyncio
    async def test_multiple_sync_checks_pass(self):
        """Test multiple synchronous checks all passing."""
        results = []

        @register_fitness_check
        def check_one():
            results.append(1)

        @register_fitness_check
        def check_two():
            results.append(2)

        await run_fitness_checks()
        assert results == [1, 2]

    @pytest.mark.asyncio
    async def test_multiple_async_checks_pass(self):
        """Test multiple asynchronous checks all passing."""
        results = []

        @register_fitness_check
        async def check_one():
            results.append(1)

        @register_fitness_check
        async def check_two():
            results.append(2)

        await run_fitness_checks()
        assert results == [1, 2]

    @pytest.mark.asyncio
    async def test_mixed_sync_async_checks_pass(self):
        """Test mixed synchronous and asynchronous checks."""
        results = []

        @register_fitness_check
        def sync_check():
            results.append("sync")

        @register_fitness_check
        async def async_check():
            results.append("async")

        await run_fitness_checks()
        assert results == ["sync", "async"]


# ============================================================================
# Execution Tests - Failure Cases
# ============================================================================

class TestFitnessExecutionFailure:
    """Tests for fitness check execution failures."""


    @pytest.mark.asyncio
    async def test_sync_check_fails(self):
        """Test that synchronous check failure causes exit."""
        @register_fitness_check
        def failing_check():
            raise RuntimeError("Check failed")

        with pytest.raises(SystemExit) as exc_info:
            await run_fitness_checks()

        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_async_check_fails(self):
        """Test that asynchronous check failure causes exit."""
        @register_fitness_check
        async def failing_check():
            raise RuntimeError("Check failed")

        with pytest.raises(SystemExit) as exc_info:
            await run_fitness_checks()

        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_first_check_passes_second_fails(self):
        """Test that first check passes but second fails."""
        first_called = False

        @register_fitness_check
        def check_one():
            nonlocal first_called
            first_called = True

        @register_fitness_check
        def check_two():
            raise RuntimeError("Second check failed")

        with pytest.raises(SystemExit) as exc_info:
            await run_fitness_checks()

        assert first_called
        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_runtime_error_caught(self):
        """Test that RuntimeError exceptions are caught and handled."""
        @register_fitness_check
        def check():
            raise RuntimeError("GPU not available")

        with pytest.raises(SystemExit) as exc_info:
            await run_fitness_checks()

        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_type_error_caught(self):
        """Test that TypeError exceptions are caught and handled."""
        @register_fitness_check
        def check():
            raise TypeError("Type mismatch")

        with pytest.raises(SystemExit) as exc_info:
            await run_fitness_checks()

        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_value_error_caught(self):
        """Test that ValueError exceptions are caught and handled."""
        @register_fitness_check
        def check():
            raise ValueError("Invalid value")

        with pytest.raises(SystemExit) as exc_info:
            await run_fitness_checks()

        assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_generic_exception_caught(self):
        """Test that generic Exception is caught and handled."""
        @register_fitness_check
        def check():
            raise Exception("Generic error")

        with pytest.raises(SystemExit) as exc_info:
            await run_fitness_checks()

        assert exc_info.value.code == 1


# ============================================================================
# Logging Tests
# ============================================================================

class TestFitnessLogging:
    """Tests for fitness check logging behavior."""


    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_fitness.log")
    async def test_logs_debug_when_no_checks(self, mock_log):
        """Test that debug log is emitted when no checks registered."""
        await run_fitness_checks()
        # Should log at least twice: system checks disabled + no checks registered
        assert mock_log.debug.call_count >= 2

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_fitness.log")
    async def test_logs_info_running_checks(self, mock_log):
        """Test that info log shows number of checks."""
        @register_fitness_check
        def check():
            pass

        await run_fitness_checks()

        # Should log "Running 1 fitness check(s)..."
        info_calls = mock_log.info.call_args_list
        assert any("Running 1 fitness check(s)" in str(call) for call in info_calls)

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_fitness.log")
    async def test_logs_check_name(self, mock_log):
        """Test that check name is logged during execution."""
        @register_fitness_check
        def my_custom_check():
            pass

        await run_fitness_checks()

        # Should log check name
        debug_calls = [str(call) for call in mock_log.debug.call_args_list]
        assert any("my_custom_check" in call for call in debug_calls)

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_fitness.log")
    async def test_logs_success_on_pass(self, mock_log):
        """Test that success is logged when check passes."""
        @register_fitness_check
        def check():
            pass

        await run_fitness_checks()

        # Should log "All fitness checks passed"
        info_calls = mock_log.info.call_args_list
        assert any("All fitness checks passed" in str(call) for call in info_calls)

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_fitness.log")
    async def test_logs_error_on_failure(self, mock_log):
        """Test that error is logged when check fails."""
        @register_fitness_check
        def failing_check():
            raise RuntimeError("Test error")

        with pytest.raises(SystemExit):
            await run_fitness_checks()

        # Should log error with check name and exception type
        error_calls = [str(call) for call in mock_log.error.call_args_list]
        assert any("failing_check" in call and "RuntimeError" in call for call in error_calls)

    @pytest.mark.asyncio
    @patch("runpod.serverless.modules.rp_fitness.log")
    async def test_logs_unhealthy_message(self, mock_log):
        """Test that unhealthy message is logged on failure."""
        @register_fitness_check
        def check():
            raise Exception("Failed")

        with pytest.raises(SystemExit):
            await run_fitness_checks()

        # Should log "Worker is unhealthy, exiting"
        error_calls = [str(call) for call in mock_log.error.call_args_list]
        assert any("unhealthy" in call.lower() for call in error_calls)


# ============================================================================
# Registry Cleanup Tests
# ============================================================================

class TestFitnessClearRegistry:
    """Tests for fitness check registry cleanup."""


    def test_clear_fitness_checks(self):
        """Test that clear_fitness_checks empties the registry."""
        @register_fitness_check
        def check_one():
            pass

        @register_fitness_check
        def check_two():
            pass

        assert len(_fitness_checks) == 2
        clear_fitness_checks()
        assert len(_fitness_checks) == 0

    def test_multiple_clear_calls(self):
        """Test that multiple clear calls don't error."""
        @register_fitness_check
        def check():
            pass

        clear_fitness_checks()
        clear_fitness_checks()  # Should not raise
        assert len(_fitness_checks) == 0


# ============================================================================
# Integration Tests
# ============================================================================

class TestFitnessIntegration:
    """Integration tests for fitness check system."""


    @pytest.mark.asyncio
    async def test_check_with_real_exception_message(self):
        """Test that real exception messages are preserved."""
        error_message = "GPU memory is exhausted"

        @register_fitness_check
        def check():
            raise RuntimeError(error_message)

        with patch("runpod.serverless.modules.rp_fitness.log") as mock_log:
            with pytest.raises(SystemExit):
                await run_fitness_checks()

            # Verify error message is logged
            error_calls = [str(call) for call in mock_log.error.call_args_list]
            assert any(error_message in call for call in error_calls)

    @pytest.mark.asyncio
    async def test_check_isolation_on_failure(self):
        """Test that failed check doesn't affect logging state."""
        results = []

        @register_fitness_check
        def check_one():
            results.append("one")

        @register_fitness_check
        def check_two():
            raise RuntimeError("Failed")

        @register_fitness_check
        def check_three():
            results.append("three")

        with pytest.raises(SystemExit):
            await run_fitness_checks()

        # Only first check should have run
        assert results == ["one"]
