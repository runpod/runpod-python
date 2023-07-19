''' Tests for runpod | serverless| worker '''

import os
import time
import argparse
from unittest.mock import patch, mock_open, Mock, MagicMock

from unittest import IsolatedAsyncioTestCase
import pytest
import nest_asyncio

import runpod
from runpod.serverless.modules.rp_logger import RunPodLogger


nest_asyncio.apply()

class TestWorker(IsolatedAsyncioTestCase):
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


class TestWorkerTestInput(IsolatedAsyncioTestCase):
    """ Tests for runpod | serverless| worker """

    def setUp(self):
        self.mock_handler = Mock()
        self.mock_handler.return_value = {}

        self.mock_handler.return_value = "test"

    def test_worker_bad_local(self):
        '''
        Test sys args.
        '''
        known_args = argparse.Namespace()
        known_args.rp_log_level = "WARN"
        known_args.rp_debugger = True
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
            assert log.level == "WARN"



def generator_handler(job):
    '''
    Test generator_handler
    '''
    print(job)
    yield "test"


@pytest.mark.asyncio
@patch("aiohttp.ClientSession")
@patch("runpod.serverless.worker.get_job")
@patch("runpod.serverless.worker.run_job")
@patch("runpod.serverless.worker.stream_result")
@patch("runpod.serverless.worker.send_result")
async def test_run_worker(
    mock_send_result, mock_stream_result, mock_run_job, mock_get_job, mock_session):
    '''
    Test run_worker

    Args:
        mock_send_result (_type_): _description_
        mock_stream_result (_type_): _description_
        mock_run_job (_type_): _description_
        mock_get_job (_type_): _description_
        mock_session (_type_): _description_
    '''

    os.environ["RUNPOD_WEBHOOK_GET_JOB"] = "https://test.com"

    # Define the mock behaviors
    mock_get_job.return_value = {"id": "123", "input": {"number": 1}}
    mock_run_job.return_value = {"output": {"result": "odd"}}

    # Set up the config
    config = {
        "handler": MagicMock(),
        "refresh_worker": True,
        "rp_args": {
            "rp_debugger": True,
            "rp_log_level": "DEBUG"
            }
        }

    # Call the function
    runpod.serverless.start(config)

    # Make assertions about the behaviors
    mock_get_job.assert_called_once()
    mock_run_job.assert_called_once()
    mock_send_result.assert_called_once()

    assert mock_stream_result.called is False
    assert mock_session.called


    # Test generator handler
    generator_config = {"handler": generator_handler, "refresh_worker": True}
    runpod.serverless.start(generator_config)
    assert mock_stream_result.called

    with patch("runpod.serverless._set_config_args") as mock_set_config_args:

        limited_config = {
            "handler": Mock(),
            "reference_counter_start": time.perf_counter(),
            "refresh_worker": True,
            "rp_args": {
                "rp_debugger": True,
                "rp_serve_api": None,
                "rp_api_port": 8000,
                "rp_api_concurrency": 1,
                "rp_api_host": "localhost"
                }
        }

        mock_set_config_args.return_value = limited_config
        runpod.serverless.start(limited_config)

        print(mock_set_config_args.call_args_list)

        assert mock_set_config_args.called


    print("HERE")
