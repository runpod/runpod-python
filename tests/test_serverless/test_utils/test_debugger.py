"""
Unit tests for the debugger utility functions.
"""

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

    def tearDown(self):
        rp_debugger.get_processor_info.cache_clear()

    @patch("cpuinfo.get_cpu_info")
    def test_processor_info_fallback_on_missing_brand(self, mock_get_cpu_info):
        """
        Missing brand_raw falls back to a placeholder instead of raising.
        """
        mock_get_cpu_info.return_value = {}
        rp_debugger.get_processor_info.cache_clear()

        self.assertEqual(
            rp_debugger.get_processor_info(), "Unable to get processor info."
        )

    @patch("cpuinfo.get_cpu_info")
    def test_processor_info_probed_once(self, mock_get_cpu_info):
        """
        The CPU probe runs on first use only and is cached afterwards.
        """
        mock_get_cpu_info.return_value = {"brand_raw": "Test CPU"}
        rp_debugger.get_processor_info.cache_clear()

        first = rp_debugger.get_processor_info()
        second = rp_debugger.get_processor_info()

        self.assertEqual((first, second), ("Test CPU", "Test CPU"))
        self.assertEqual(mock_get_cpu_info.call_count, 1)

    @patch("cpuinfo.get_cpu_info")
    def test_debugger_output_includes_processor(self, mock_get_cpu_info):
        """
        Debugger output reports the probed processor name.
        """
        mock_get_cpu_info.return_value = {"brand_raw": "Test CPU"}
        rp_debugger.get_processor_info.cache_clear()

        system_info = get_debugger_output()["system_info"]

        self.assertEqual(system_info["processor"], "Test CPU")

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
