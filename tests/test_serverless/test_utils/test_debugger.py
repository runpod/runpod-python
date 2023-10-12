'''
Unit tests for the debugger utility functions.
'''

import time
import unittest
import importlib
from unittest.mock import patch

from runpod.serverless.utils import rp_debugger
from runpod.serverless.utils.rp_debugger import(
    Checkpoints, LineTimer, FunctionTimer, get_debugger_output, clear_debugger_output
)


class TestDebugger(unittest.TestCase):
    ''' Unit tests for the debugger utility functions. '''

    def setUp(self):
        self.checkpoints = Checkpoints()
        self.checkpoints.clear()

    @patch('runpod.serverless.utils.rp_debugger.cpuinfo.get_cpu_info')
    def test_key_error(self, mock_get_cpu_info):
        '''
        Test that a KeyError is raised when an invalid key is used.
        '''
        mock_get_cpu_info.side_effect = KeyError('Test Error')

        importlib.reload(rp_debugger)

        assert mock_get_cpu_info.called
        self.assertEqual(rp_debugger.PROCESSOR, 'Unable to get processor info.')

    def test_checkpoints(self):
        '''
        Test that checkpoints are added and stopped correctly.
        '''
        self.checkpoints.add('checkpoint1')
        self.checkpoints.start('checkpoint1')
        time.sleep(0.1)
        self.checkpoints.stop('checkpoint1')

        output = get_debugger_output()['timestamps']

        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['name'], 'checkpoint1')
        self.assertGreater(output[0]['duration_ms'], 0)

    def test_get_checkpoints(self):
        '''
        Test that checkpoints are added and stopped correctly.
        '''
        self.checkpoints.add('checkpoint1')
        checkpoint_list = self.checkpoints.get_checkpoints()

        self.assertEqual(len(checkpoint_list), 0)


    def test_checkpoints_exception(self):
        '''
        Test that a KeyError is raised when an invalid checkpoint is used.
        '''
        self.assertRaises(KeyError, self.checkpoints.start, 'nonexistent')
        self.assertRaises(KeyError, self.checkpoints.stop, 'nonexistent')

        self.checkpoints.add('checkpoint2')
        self.assertRaises(KeyError, self.checkpoints.add, 'checkpoint2')
        self.assertRaises(KeyError, self.checkpoints.stop, 'checkpoint2')

    def test_line_timer(self):
        '''
        Test that the line timer works correctly.
        '''
        with LineTimer('line_timer'):
            time.sleep(0.1)

        output = get_debugger_output()['timestamps']

        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['name'], 'line_timer')
        self.assertGreater(output[0]['duration_ms'], 0)

    def test_function_timer(self):
        '''
        Test that the function timer works correctly.
        '''
        @FunctionTimer
        def func_to_time():
            time.sleep(0.1)

        func_to_time()

        output = get_debugger_output()['timestamps']

        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]['name'], 'func_to_time')
        self.assertGreater(output[0]['duration_ms'], 0)


    def test_clear_debugger_output(self):
        '''
        Test that the debugger output is cleared correctly.
        '''
        self.checkpoints.add('checkpoint1')
        self.checkpoints.start('checkpoint1')

        # Check that non-stopped checkpoints are not returned
        checkpoint_list = self.checkpoints.get_checkpoints()
        self.assertEqual(len(checkpoint_list), 0)

        self.checkpoints.stop('checkpoint1')

        checkpoint_list = self.checkpoints.get_checkpoints()
        self.assertEqual(len(checkpoint_list), 1)

        clear_debugger_output()

        checkpoint_list = self.checkpoints.get_checkpoints()
        self.assertEqual(len(checkpoint_list), 0)

if __name__ == "__main__":
    unittest.main()
