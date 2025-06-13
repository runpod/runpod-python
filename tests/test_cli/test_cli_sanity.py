"""
CLI Sanity Checks

These tests ensure that basic CLI operations work correctly and efficiently.
"""

import subprocess
import pytest
from click.testing import CliRunner

from runpod.cli.entry import runpod_cli


@pytest.fixture
def cli_runner():
    """Provide a Click CLI runner for testing."""
    return CliRunner()


@pytest.fixture(autouse=True)
def reset_jobs_progress():
    """Reset JobsProgress state before each test."""
    yield
    # Cleanup after test
    try:
        from runpod.serverless.modules.worker_state import reset_jobs_progress, JobsProgress
        from runpod.serverless.modules.rp_ping import reset_heartbeat
        reset_jobs_progress()
        reset_heartbeat()
        # Also reset the singleton instance
        if hasattr(JobsProgress, '_instance'):
            JobsProgress._instance = None
    except (ImportError, AttributeError):
        pass


class TestCLISanity:
    """Test basic CLI functionality and import safety"""

    def test_help_command_works(self, cli_runner):
        """
        Test that --help commands work correctly for all CLI commands.
        """
        
        # Test main help
        result = cli_runner.invoke(runpod_cli, ["--help"])
        assert result.exit_code == 0, f"Main --help failed: {result.output}"
        assert "A collection of CLI functions for RunPod" in result.output
        
        # Test subcommand help
        result = cli_runner.invoke(runpod_cli, ["pod", "--help"])
        assert result.exit_code == 0, f"Pod --help failed: {result.output}"
        assert "Manage and interact with pods" in result.output
        
        result = cli_runner.invoke(runpod_cli, ["config", "--help"])
        assert result.exit_code == 0, f"Config --help failed: {result.output}"
        
        result = cli_runner.invoke(runpod_cli, ["project", "--help"])
        assert result.exit_code == 0, f"Project --help failed: {result.output}"
        
        result = cli_runner.invoke(runpod_cli, ["ssh", "--help"])
        assert result.exit_code == 0, f"SSH --help failed: {result.output}"
        
        result = cli_runner.invoke(runpod_cli, ["exec", "--help"])
        assert result.exit_code == 0, f"Exec --help failed: {result.output}"

    def test_help_command_subprocess(self):
        """
        Test --help commands using subprocess to ensure they work in real-world usage.
        """
        # Test main help using the installed runpod command
        result = subprocess.run(
            ["runpod", "--help"],
            capture_output=True,
            text=True,
            timeout=10  # Prevent hanging
        )
        assert result.returncode == 0, f"Subprocess --help failed: {result.stderr}"
        assert "A collection of CLI functions for RunPod" in result.stdout
        
        # Test pod help
        result = subprocess.run(
            ["runpod", "pod", "--help"],
            capture_output=True,
            text=True,
            timeout=10
        )
        assert result.returncode == 0, f"Subprocess pod --help failed: {result.stderr}"
        assert "Manage and interact with pods" in result.stdout

    def test_import_safety(self):
        """
        Test that importing runpod modules works correctly.
        """
        # Test importing main package
        try:
            import runpod  # noqa: F401  # pylint: disable=import-outside-toplevel,unused-import
            # Import successful if no exception raised
        except Exception as e:
            pytest.fail(f"Failed to import runpod: {e}")
        
        # Test importing serverless modules
        try:
            from runpod.serverless.modules.worker_state import JobsProgress
            jobs = JobsProgress()
            # JobsProgress should be properly instantiated (no exception = success)
        except Exception as e:
            pytest.fail(f"Failed to import/instantiate JobsProgress: {e}")
        
        # Test that operations work correctly
        try:
            from runpod.serverless.modules.worker_state import JobsProgress
            jobs = JobsProgress()
            
            # Basic operations should work
            count = jobs.get_job_count()
            assert count == 0
            
            # Verify the instance has the expected mode attributes
            assert isinstance(jobs._use_multiprocessing, bool), "Should have _use_multiprocessing boolean flag"
            
            # Test adding and retrieving jobs
            jobs.add({'id': 'test-job'})
            assert jobs.get_job_count() == 1
            job_list = jobs.get_job_list()
            assert job_list == 'test-job'
            
            # Clean up
            jobs.clear()
            assert jobs.get_job_count() == 0
            
        except Exception as e:
            pytest.fail(f"JobsProgress operations failed: {e}")

    def test_cli_entry_point_import(self):
        """
        Test that the CLI entry point can be imported without issues.
        """
        try:
            from runpod.cli.entry import runpod_cli
            assert callable(runpod_cli), "runpod_cli should be callable"
        except Exception as e:
            pytest.fail(f"Failed to import CLI entry point: {e}")
