''' Tests for rp_local.py '''

import pytest
import unittest
from unittest.mock import patch, mock_open

from runpod.serverless.modules import rp_local


class TestRunLocal(unittest.TestCase):
    @patch("runpod.serverless.modules.rp_local.run_job", return_value={})
    @patch("builtins.open", new_callable=mock_open, read_data='{"input": "test"}')
    def test_run_local_with_test_input(self, mock_file, mock_run):
        '''
        Test run_local function with test_input in rp_args
        '''
        config = {
            "handler": "handler",
            "rp_args": {
                "test_input": {
                    "input": "test",
                    "id": "test_id"
                }
            }
        }
        with self.assertRaises(SystemExit) as cm:
            rp_local.run_local(config)
        self.assertEqual(cm.exception.code, 0)

    @patch("runpod.serverless.modules.rp_local.run_job", return_value={})
    @patch("builtins.open", new_callable=mock_open, read_data='{"input": "test"}')
    def test_run_local_with_test_input_json(self, mock_file, mock_run):
        '''
        Test run_local function with test_input.json
        '''
        config = {
            "handler": "handler",
            "rp_args": {}
        }
        with patch("os.path.exists", return_value=True):
            with self.assertRaises(SystemExit) as cm:
                rp_local.run_local(config)
            self.assertEqual(cm.exception.code, 0)

    @patch("runpod.serverless.modules.rp_local.run_job", return_value={"error": "test_error"})
    @patch("builtins.open", new_callable=mock_open, read_data='{"input": "test"}')
    def test_run_local_with_error(self, mock_file, mock_run):
        '''
        Test run_local function when run_job returns an error
        '''
        config = {
            "handler": "handler",
            "rp_args": {
                "test_input": {
                    "input": "test",
                    "id": "test_id"
                }
            }
        }
        with self.assertRaises(SystemExit) as cm:
            rp_local.run_local(config)
        self.assertEqual(cm.exception.code, 1)

    def test_run_local_without_test_input_json(self):
        '''
        Test run_local function without test_input.json
        '''
        config = {
            "handler": "handler",
            "rp_args": {}
        }
        with patch("os.path.exists", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                rp_local.run_local(config)
            self.assertEqual(cm.exception.code, 1)

    @patch("runpod.serverless.modules.rp_local.run_job", return_value={})
    @patch("builtins.open", new_callable=mock_open, read_data='{"not_input": "test"}')
    def test_run_local_without_input(self, mock_file, mock_run):
        '''
        Test run_local function without input in test_input.json
        '''
        config = {
            "handler": "handler",
            "rp_args": {}
        }
        with patch("os.path.exists", return_value=True):
            with self.assertRaises(SystemExit) as cm:
                rp_local.run_local(config)
            self.assertEqual(cm.exception.code, 1)
