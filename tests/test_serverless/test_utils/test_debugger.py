"""
Unit tests for the debugger utility functions.
"""

import importlib
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from runpod.serverless.utils import rp_debugger
from runpod.serverless.utils.rp_debugger import (
    Checkpoints,
    FunctionTimer,
    LineTimer,
    clear_debugger_output,
    get_debugger_output,
)


class TestDebugger(unittest.TestCase):
    """Unit tests for the debugger utility functions."""

    def setUp(self):
        self.checkpoints = Checkpoints()
        self.checkpoints.clear()
        rp_debugger._get_processor.cache_clear()

    def tearDown(self):
        rp_debugger._get_processor.cache_clear()

    @patch("cpuinfo.get_cpu_info")
    def test_import_does_not_probe_cpu(self, mock_get_cpu_info):
        """
        Importing the module must not run py-cpuinfo; on x86 it sleeps 1s.
        """
        importlib.reload(rp_debugger)

        mock_get_cpu_info.assert_not_called()

    def test_import_does_not_load_cpuinfo(self):
        """
        Importing the module must not import py-cpuinfo, which spawns a subprocess at import.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import runpod.serverless.utils.rp_debugger; "
                "print('cpuinfo' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(result.stdout.strip(), "False")

    @patch("cpuinfo.get_cpu_info")
    def test_debugger_output_reports_processor(self, mock_get_cpu_info):
        """
        The processor is looked up when debugger output is built.
        """
        mock_get_cpu_info.return_value = {"brand_raw": "Test CPU"}

        output = get_debugger_output()

        self.assertEqual(output["system_info"]["processor"], "Test CPU")

    @patch("cpuinfo.get_cpu_info")
    def test_processor_lookup_is_cached(self, mock_get_cpu_info):
        """
        The processor is probed once per process, not once per job.
        """
        mock_get_cpu_info.return_value = {"brand_raw": "Test CPU"}

        get_debugger_output()
        get_debugger_output()

        mock_get_cpu_info.assert_called_once()

    @patch("cpuinfo.get_cpu_info")
    def test_key_error(self, mock_get_cpu_info):
        """
        A missing brand string falls back to a placeholder.
        """
        mock_get_cpu_info.side_effect = KeyError("Test Error")

        output = get_debugger_output()

        self.assertEqual(
            output["system_info"]["processor"], "Unable to get processor info."
        )

    @patch("cpuinfo.get_cpu_info")
    def test_processor_attribute_is_lazy(self, mock_get_cpu_info):
        """
        The PROCESSOR module attribute still resolves for existing callers.
        """
        mock_get_cpu_info.return_value = {"brand_raw": "Test CPU"}

        self.assertEqual(rp_debugger.PROCESSOR, "Test CPU")

    def test_checkpoints(self):
        """
        Test that checkpoints are added and stopped correctly.
        """
        self.checkpoints.add("checkpoint1")
        self.checkpoints.start("checkpoint1")
        time.sleep(0.1)
        self.checkpoints.stop("checkpoint1")

        output = get_debugger_output()["timestamps"]

        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["name"], "checkpoint1")
        self.assertGreater(output[0]["duration_ms"], 0)

    def test_get_checkpoints(self):
        """
        Test that checkpoints are added and stopped correctly.
        """
        self.checkpoints.add("checkpoint1")
        checkpoint_list = self.checkpoints.get_checkpoints()

        self.assertEqual(len(checkpoint_list), 0)

    def test_checkpoints_exception(self):
        """
        Test that a KeyError is raised when an invalid checkpoint is used.
        """
        self.assertRaises(KeyError, self.checkpoints.start, "nonexistent")
        self.assertRaises(KeyError, self.checkpoints.stop, "nonexistent")

        self.checkpoints.add("checkpoint2")
        self.assertRaises(KeyError, self.checkpoints.add, "checkpoint2")
        self.assertRaises(KeyError, self.checkpoints.stop, "checkpoint2")

    def test_line_timer(self):
        """
        Test that the line timer works correctly.
        """
        with LineTimer("line_timer"):
            time.sleep(0.1)

        output = get_debugger_output()["timestamps"]

        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["name"], "line_timer")
        self.assertGreater(output[0]["duration_ms"], 0)

    def test_function_timer(self):
        """
        Test that the function timer works correctly.
        """

        @FunctionTimer
        def func_to_time():
            time.sleep(0.1)

        func_to_time()

        output = get_debugger_output()["timestamps"]

        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["name"], "func_to_time")
        self.assertGreater(output[0]["duration_ms"], 0)

    def test_clear_debugger_output(self):
        """
        Test that the debugger output is cleared correctly.
        """
        self.checkpoints.add("checkpoint1")
        self.checkpoints.start("checkpoint1")

        # Check that non-stopped checkpoints are not returned
        checkpoint_list = self.checkpoints.get_checkpoints()
        self.assertEqual(len(checkpoint_list), 0)

        self.checkpoints.stop("checkpoint1")

        checkpoint_list = self.checkpoints.get_checkpoints()
        self.assertEqual(len(checkpoint_list), 1)

        clear_debugger_output()

        checkpoint_list = self.checkpoints.get_checkpoints()
        self.assertEqual(len(checkpoint_list), 0)


if __name__ == "__main__":
    unittest.main()
