'''
Unit tests for the debugger utility functions.
'''
# pylint: disable=missing-docstring

import time
import unittest
from runpod.serverless.utils.rp_debugger import(
    Checkpoints, LineTimer, FunctionTimer, get_debugger_output
)


class TestDebugger(unittest.TestCase):
    def setUp(self):
        self.checkpoints = Checkpoints()
        self.checkpoints.clear()

    def test_checkpoints(self):
        self.checkpoints.add('checkpoint1')
        self.checkpoints.start('checkpoint1')
        time.sleep(0.1)
        self.checkpoints.stop('checkpoint1')

        output = get_debugger_output()['timestamps']

        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['name'], 'checkpoint1')
        self.assertGreater(output[0]['duration_ms'], 0)

    def test_checkpoints_exception(self):
        self.assertRaises(KeyError, self.checkpoints.start, 'nonexistent')
        self.assertRaises(KeyError, self.checkpoints.stop, 'nonexistent')

        self.checkpoints.add('checkpoint2')
        self.assertRaises(KeyError, self.checkpoints.add, 'checkpoint2')
        self.assertRaises(KeyError, self.checkpoints.stop, 'checkpoint2')

    def test_line_timer(self):
        with LineTimer('line_timer'):
            time.sleep(0.1)

        output = get_debugger_output()['timestamps']

        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['name'], 'line_timer')
        self.assertGreater(output[0]['duration_ms'], 0)

    def test_function_timer(self):
        @FunctionTimer
        def func_to_time():
            time.sleep(0.1)

        func_to_time()

        output = get_debugger_output()['timestamps']

        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['name'], 'func_to_time')
        self.assertGreater(output[0]['duration_ms'], 0)


if __name__ == "__main__":
    unittest.main()
