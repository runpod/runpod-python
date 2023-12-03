''' Tests for runpod | serverless| worker '''
# pylint: disable=protected-access

import os
import argparse
from unittest.mock import patch, mock_open, Mock, MagicMock

from unittest import IsolatedAsyncioTestCase
import nest_asyncio

import runpod
from runpod.serverless.modules.rp_logger import RunPodLogger
from runpod.serverless import _signal_handler

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
            assert runpod.serverless.worker._get_auth_header(
            ) == {'Authorization': 'test'}

    def test_is_local(self):
        '''
        Test _is_local
        '''
        with patch("runpod.serverless.worker.os") as mock_os:
            mock_os.environ.get.return_value = None
            assert runpod.serverless.worker._is_local(
                {"rp_args": {}}) is True
            assert runpod.serverless.worker._is_local(
                {"rp_args": {"test_input": "something"}}) is True

            mock_os.environ.get.return_value = "something"
            assert runpod.serverless.worker._is_local(
                self.mock_config) is False

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
            assert runpod.serverless.worker._is_local(
                self.mock_config) is True
            mock_os.environ.get.return_value = "something"
            assert runpod.serverless.worker._is_local(
                self.mock_config) is False

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

    @patch('runpod.serverless.log')
    @patch('runpod.serverless.sys.exit')
    def test_signal_handler(self, mock_exit, mock_logger):
        '''
        Test signal handler.
        '''

        _signal_handler(None, None)

        assert mock_exit.called
        assert mock_logger.info.called


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
        known_args.test_output = '{"test": "test"}'

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
    yield "test1"
    yield "test2"


def generator_handler_exception(job):
    '''
    Test generator_handler
    '''
    print(job)
    yield "test1"
    raise Exception()  # pylint: disable=broad-exception-raised


class TestRunWorker(IsolatedAsyncioTestCase):
    """ Tests for runpod | serverless| worker """

    def setUp(self):
        os.environ["RUNPOD_WEBHOOK_GET_JOB"] = "https://test.com"

        # Set up the config
        self.config = {
            "handler": MagicMock(),
            "refresh_worker": True,
            "rp_args": {
                "rp_debugger": True,
                "rp_log_level": "DEBUG"
            }
        }

    @patch("aiohttp.ClientSession")
    @patch("runpod.serverless.modules.rp_scale.get_job")
    @patch("runpod.serverless.worker.run_job")
    @patch("runpod.serverless.worker.stream_result")
    @patch("runpod.serverless.worker.send_result")
    # pylint: disable=too-many-arguments
    async def test_run_worker(
            self, mock_send_result, mock_stream_result, mock_run_job, mock_get_job, mock_session):
        '''
        Test run_worker with synchronous handler.

        Args:
            mock_send_result (_type_): _description_
            mock_stream_result (_type_): _description_
            mock_run_job (_type_): _description_
            mock_get_job (_type_): _description_
            mock_session (_type_): _description_
        '''
        # Define the mock behaviors
        mock_get_job.return_value = {"id": "123", "input": {"number": 1}}
        mock_run_job.return_value = {"output": {"result": "odd"}}

        # Call the function
        runpod.serverless.start(self.config)

        # Make assertions about the behaviors
        mock_get_job.assert_called_once()
        mock_run_job.assert_called_once()
        mock_send_result.assert_called_once()

        assert mock_stream_result.called is False
        assert mock_session.called

    @patch("runpod.serverless.modules.rp_scale.get_job")
    @patch("runpod.serverless.worker.run_job")
    @patch("runpod.serverless.worker.stream_result")
    @patch("runpod.serverless.worker.send_result")
    async def test_run_worker_generator_handler(
            self, mock_send_result, mock_stream_result, mock_run_job,
            mock_get_job):
        '''
        Test run_worker with generator handler.

        Args:
            mock_stream_result (_type_): _description_
            mock_run_job_generator (_type_): _description_
            mock_run_job (_type_): _description_
            mock_get_job (_type_): _description_
        '''
        # Define the mock behaviors
        mock_get_job.return_value = {
            "id": "generator-123", "input": {"number": 1}}

        # Test generator handler
        generator_config = {
            "handler": generator_handler, "refresh_worker": True}
        runpod.serverless.start(generator_config)

        assert mock_stream_result.called
        assert not mock_run_job.called

        # Since return_aggregate_stream is NOT activated, we should not submit any outputs.
        _, args, _ = mock_send_result.mock_calls[0]
        assert args[1] == {'output': [], 'stopPod': True}

    @patch("runpod.serverless.modules.rp_scale.get_job")
    @patch("runpod.serverless.worker.run_job")
    @patch("runpod.serverless.worker.stream_result")
    @patch("runpod.serverless.worker.send_result")
    async def test_run_worker_generator_handler_exception(
            self, mock_send_result, mock_stream_result, mock_run_job,
            mock_get_job):
        '''
        Test run_worker with generator handler.

        Args:
            mock_stream_result (_type_): _description_
            mock_run_job_generator (_type_): _description_
            mock_run_job (_type_): _description_
            mock_get_job (_type_): _description_
        '''
        # Define the mock behaviors
        mock_get_job.return_value = {
            "id": "generator-123", "input": {"number": 1}}

        # Test generator handler
        generator_config = {
            "handler": generator_handler_exception, "refresh_worker": True}
        runpod.serverless.start(generator_config)

        assert mock_stream_result.call_count == 1
        assert not mock_run_job.called

        # Since return_aggregate_stream is NOT activated, we should not submit any outputs.
        _, args, _ = mock_send_result.mock_calls[0]
        assert 'error' in args[1]

    @patch("runpod.serverless.modules.rp_scale.get_job")
    @patch("runpod.serverless.worker.run_job")
    @patch("runpod.serverless.worker.stream_result")
    @patch("runpod.serverless.worker.send_result")
    async def test_run_worker_generator_aggregate_handler(
            self, mock_send_result, mock_stream_result, mock_run_job,
            mock_get_job):
        '''
        Test run_worker with generator handler.

        Args:
            mock_send_result (_type_): _description_
            mock_stream_result (_type_): _description_
            mock_run_job (_type_): _description_
            mock_get_job (_type_): _description_
            mock_session (_type_): _description_
        '''
        # Define the mock behaviors
        mock_get_job.return_value = {
            "id": "generator-123", "input": {"number": 1}}

        # Test generator handler
        generator_config = {
            "handler": generator_handler, "return_aggregate_stream": True, "refresh_worker": True}
        runpod.serverless.start(generator_config)

        assert mock_send_result.called
        assert mock_stream_result.called
        assert not mock_run_job.called

        # Since return_aggregate_stream is activated, we should submit a list of the outputs.
        _, args, _ = mock_send_result.mock_calls[0]
        assert args[1] == {'output': ['test1', 'test2'], 'stopPod': True}

    @patch("aiohttp.ClientSession")
    @patch("runpod.serverless.modules.rp_scale.get_job")
    @patch("runpod.serverless.worker.run_job")
    @patch("runpod.serverless.worker.stream_result")
    @patch("runpod.serverless.worker.send_result")
    # pylint: disable=too-many-arguments
    async def test_run_worker_concurrency(
            self, mock_send_result, mock_stream_result, mock_run_job, mock_get_job, mock_session):
        '''
        Test run_worker with synchronous handler.

        Args:
            mock_send_result (_type_): _description_
            mock_stream_result (_type_): _description_
            mock_run_job (_type_): _description_
            mock_get_job (_type_): _description_
            mock_session (_type_): _description_
        '''
        # Define the mock behaviors
        mock_get_job.return_value = {"id": "123", "input": {"number": 1}}
        mock_run_job.return_value = {"output": {"result": "odd"}}

        def concurrency_modifier(current_concurrency):
            return current_concurrency + 1

        config_with_concurrency = self.config.copy()
        config_with_concurrency['concurrency_modifier'] = concurrency_modifier

        # Call the function
        runpod.serverless.start(config_with_concurrency)

        # Make assertions about the behaviors
        mock_get_job.assert_called_once()
        mock_run_job.assert_called_once()
        mock_send_result.assert_called_once()

        assert mock_stream_result.called is False
        assert mock_session.called
