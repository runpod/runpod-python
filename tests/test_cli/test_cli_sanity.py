"""
CLI Sanity Checks

These tests ensure that basic CLI operations work correctly and efficiently.
"""

import subprocess
import sys
import unittest
from click.testing import CliRunner

from runpod.cli.entry import runpod_cli


class TestCLISanity(unittest.TestCase):
    """Test basic CLI functionality and import safety"""

    def test_help_command_works(self):
        """
        Test that --help commands work correctly for all CLI commands.
        """
        runner = CliRunner()
        
        # Test main help
        result = runner.invoke(runpod_cli, ["--help"])
        self.assertEqual(result.exit_code, 0, f"Main --help failed: {result.output}")
        self.assertIn("A collection of CLI functions for RunPod", result.output)
        
        # Test subcommand help
        result = runner.invoke(runpod_cli, ["pod", "--help"])
        self.assertEqual(result.exit_code, 0, f"Pod --help failed: {result.output}")
        self.assertIn("Manage and interact with pods", result.output)
        
        result = runner.invoke(runpod_cli, ["config", "--help"])
        self.assertEqual(result.exit_code, 0, f"Config --help failed: {result.output}")
        
        result = runner.invoke(runpod_cli, ["project", "--help"])
        self.assertEqual(result.exit_code, 0, f"Project --help failed: {result.output}")
        
        result = runner.invoke(runpod_cli, ["ssh", "--help"])
        self.assertEqual(result.exit_code, 0, f"SSH --help failed: {result.output}")
        
        result = runner.invoke(runpod_cli, ["exec", "--help"])
        self.assertEqual(result.exit_code, 0, f"Exec --help failed: {result.output}")

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
        self.assertEqual(result.returncode, 0, 
                        f"Subprocess --help failed: {result.stderr}")
        self.assertIn("A collection of CLI functions for RunPod", result.stdout)
        
        # Test pod help
        result = subprocess.run(
            ["runpod", "pod", "--help"],
            capture_output=True,
            text=True,
            timeout=10
        )
        self.assertEqual(result.returncode, 0, 
                        f"Subprocess pod --help failed: {result.stderr}")
        self.assertIn("Manage and interact with pods", result.stdout)

    def test_import_safety(self):
        """
        Test that importing runpod modules works correctly.
        """
        # Test importing main package
        try:
            import runpod
            self.assertTrue(True, "Main runpod import successful")
        except Exception as e:
            self.fail(f"Failed to import runpod: {e}")
        
        # Test importing serverless modules
        try:
            from runpod.serverless.modules.worker_state import JobsProgress
            jobs = JobsProgress()
            # Ensure lazy initialization is working - storage should be None until first use
            self.assertIsNone(jobs._storage, 
                            "Storage backend should not be created until first use")
            self.assertTrue(True, "JobsProgress import and instantiation successful")
        except Exception as e:
            self.fail(f"Failed to import/instantiate JobsProgress: {e}")
        
        # Test that operations trigger proper initialization
        try:
            from runpod.serverless.modules.worker_state import JobsProgress
            jobs = JobsProgress()
            # Initially no storage backend
            self.assertIsNone(jobs._storage, "Storage should be None initially")
            
            # First operation should initialize storage backend
            count = jobs.get_job_count()
            self.assertEqual(count, 0)
            self.assertIsNotNone(jobs._storage, 
                               "Storage backend should be created after first operation")
            
            # Verify storage backend is one of the expected types
            from runpod.serverless.modules.worker_state import _MultiprocessingStorage, _ThreadSafeStorage
            self.assertIsInstance(jobs._storage, (_MultiprocessingStorage, _ThreadSafeStorage),
                                "Storage should be either multiprocessing or thread-safe backend")
        except Exception as e:
            self.fail(f"Storage initialization failed: {e}")

    def test_cli_entry_point_import(self):
        """
        Test that the CLI entry point can be imported without issues.
        """
        try:
            from runpod.cli.entry import runpod_cli
            self.assertTrue(callable(runpod_cli), "runpod_cli should be callable")
        except Exception as e:
            self.fail(f"Failed to import CLI entry point: {e}")


if __name__ == "__main__":
    unittest.main() 