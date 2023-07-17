''' Tests for runpod | serverless| worker '''

import argparse

import unittest
from unittest.mock import patch, mock_open, Mock

import runpod
from runpod.serverless.modules.rp_logger import RunPodLogger


class TestWorker(unittest.TestCase):
    """ Tests for runpod | serverless| worker """

    def setUp(self):
        self.mock_handler = Mock()
        self.mock_handler.return_value = "test"

        self.mock_config = Mock()
        self.mock_config = {
            "handler": self.mock_handler,
            "rp_args": {
                "test_input": None,
            }
        }

    def test_get_auth_header(self):
        '''
        Test _get_auth_header
        '''
        with patch("runpod.serverless.worker.os") as mock_os:
            mock_os.environ.get.return_value = "test"

            auth_header = runpod.serverless.worker._get_auth_header() # pylint: disable=protected-access
            assert auth_header == {'Authorization': 'test'}

    def test_is_local(self):
        '''
        Test _is_local
        '''
        with patch("runpod.serverless.worker.os") as mock_os:
            mock_os.environ.get.return_value = None
            assert runpod.serverless.worker._is_local({"rp_args": {}}) is True # pylint: disable=protected-access
            assert runpod.serverless.worker._is_local({"rp_args":{"test_input": "something"}}) is True # pylint: disable=protected-access, line-too-long

            mock_os.environ.get.return_value = "something"
            assert runpod.serverless.worker._is_local(self.mock_config) is False # pylint: disable=protected-access

    def test_start(self):
        '''
        Test basic start call.
        '''
        with patch("builtins.open", mock_open(read_data='{"input":{"number":1}}')) as mock_file, \
            self.assertRaises(SystemExit):

            runpod.serverless.start({"handler": self.mock_handler})

            assert mock_file.called

    def test_is_local_testing(self):
        '''
        Test _is_local_testing
        '''
        with patch("runpod.serverless.worker.os") as mock_os:
            mock_os.environ.get.return_value = None
            assert runpod.serverless.worker._is_local(self.mock_config) is True # pylint: disable=protected-access

            mock_os.environ.get.return_value = "something"
            assert runpod.serverless.worker._is_local(self.mock_config) is False # pylint: disable=protected-access

    def test_local_api(self):
        '''
        Test local FastAPI setup.
        '''

        known_args = argparse.Namespace()
        known_args.rp_log_level = None
        known_args.rp_debugger = None
        known_args.rp_serve_api = True
        known_args.rp_api_port = 8000
        known_args.rp_api_concurrency = 1
        known_args.rp_api_host = "localhost"
        known_args.test_input = '{"test": "test"}'

        with patch("argparse.ArgumentParser.parse_known_args") as mock_parse_known_args, \
            patch("runpod.serverless.rp_fastapi") as mock_fastapi:

            mock_parse_known_args.return_value = known_args, []
            runpod.serverless.start({"handler": self.mock_handler})

            assert mock_fastapi.WorkerAPI.called


class TestWorkerTestInput(unittest.TestCase):
    """ Tests for runpod | serverless| worker """

    def setUp(self):
        self.mock_handler = Mock()
        self.mock_handler.return_value = "test"

    def test_worker_bad_local(self):
        '''
        Test sys args.
        '''
        known_args = argparse.Namespace()
        known_args.rp_log_level = "WARN"
        known_args.rp_debugger = None
        known_args.rp_serve_api = None
        known_args.rp_api_port = 8000
        known_args.rp_api_concurrency = 1
        known_args.rp_api_host = "localhost"
        known_args.test_input = '{"test": "test"}'

        with patch("argparse.ArgumentParser.parse_known_args") as mock_parse_known_args, \
            self.assertRaises(SystemExit):

            mock_parse_known_args.return_value = known_args, []
            runpod.serverless.start({"handler": self.mock_handler})

            # Confirm that the log level is set to WARN
            log = RunPodLogger()
            assert log.level() == "WARN"
