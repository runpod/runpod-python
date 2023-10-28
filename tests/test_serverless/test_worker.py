''' Tests for runpod | serverless| worker '''
import os
import time
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
            assert runpod.serverless.worker._get_auth_header() == {'Authorization': 'test'}  # pylint: disable=protected-access

    def test_is_local(self):
        '''
        Test _is_local
        '''
        with patch("runpod.serverless.worker.os") as mock_os:
            mock_os.environ.get.return_value = None
            assert runpod.serverless.worker._is_local({"rp_args": {}}) is True  # pylint: disable=protected-access
            assert runpod.serverless.worker._is_local({"rp_args": {"test_input": "something"}}) is True  # pylint: disable=protected-access, line-too-long

            mock_os.environ.get.return_value = "something"
            assert runpod.serverless.worker._is_local(self.mock_config) is False  # pylint: disable=protected-access

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
            assert runpod.serverless.worker._is_local(self.mock_config) is True  # pylint: disable=protected-access

            mock_os.environ.get.return_value = "something"
            assert runpod.serverless.worker._is_local(self.mock_config) is False  # pylint: disable=protected-access

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
    raise Exception() # pylint: disable=broad-exception-raised


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
    async def test_run_worker_multi_processing(
            self, mock_send_result, mock_stream_result, mock_run_job, mock_get_job, mock_session):
        '''
        Test run_worker with multi processing enabled, both async and generator handler.

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

        # Include multi-processing inside config
        def concurrency_controller():
            return False

        # Include the concurrency_controller
        self.config['concurrency_controller'] = concurrency_controller

        # Call the function
        runpod.serverless.start(self.config)

        # Make assertions about the behaviors
        mock_get_job.assert_called_once()
        mock_run_job.assert_called_once()
        mock_send_result.assert_called_once()

        assert mock_stream_result.called is False
        assert mock_session.called

        # Test generator handler
        generator_config = {
            "handler": generator_handler,
            "refresh_worker": True,
            "concurrency_controller": concurrency_controller
        }
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

    @patch("runpod.serverless.modules.rp_scale.get_job")
    @patch("runpod.serverless.worker.run_job")
    @patch("runpod.serverless.worker.send_result")
    async def test_run_worker_multi_processing_scaling_up(
            self, mock_send_result, mock_run_job, mock_get_job):
        '''
        Test run_worker with multi processing enabled, the scale-up and scale-down
        behavior with concurrency_controller.

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

        # Include multi-processing inside config
        # Should go from concurrency 1 -> 2 -> 4 -> 8 -> 16 -> 8 -> 4 -> 2 -> 1
        # 1+2+4+8+16+8+4+2+1 -> 46 calls to get_job.
        scale_behavior = {
            'behavior': [False, False, False, False, False, False, True, True, True, True, True],
            'counter': 0,
        }

        def concurrency_controller():
            val = scale_behavior['behavior'][scale_behavior['counter']]
            return val

        # Let the test be a long running one so we can capture the scale-up and scale-down.
        config = {
            "handler": MagicMock(),
            "refresh_worker": False,
            "concurrency_controller": concurrency_controller,
            "rp_args": {
                "rp_debugger": True,
                "rp_log_level": "DEBUG"
            }
        }

        # Let's mock job_scaler.is_alive so that it returns False
        # when scale_behavior's counter is now 5.
        def mock_is_alive():
            res = scale_behavior['counter'] < 10
            scale_behavior['counter'] += 1
            return res

        with patch("runpod.serverless.modules.rp_scale.JobScaler.is_alive", wraps=mock_is_alive):
            runpod.serverless.start(config)

        # Assert that the mock_get_job, mock_run_job, and mock_send_result is called
        # 1 + 2 + 4 + 8 + 16 + 8 + 4 + 2 + 1 = 46 times
        assert mock_get_job.call_count == 46
        assert mock_run_job.call_count == 46
        assert mock_send_result.call_count == 46

    @patch("runpod.serverless.modules.rp_scale.get_job")
    @patch("runpod.serverless.worker.run_job")
    @patch("runpod.serverless.worker.send_result")
    async def test_run_worker_multi_processing_availability_ratio(
            self, mock_send_result, mock_run_job, mock_get_job):
        '''
        Test run_worker with multi processing enabled, the scale-up and
        scale-down behavior with availability ratio.

        Args:
            mock_send_result (_type_): _description_
            mock_stream_result (_type_): _description_
            mock_run_job (_type_): _description_
            mock_get_job (_type_): _description_
            mock_session (_type_): _description_
        '''
        # For downscaling, we'll rely entirely on the availability ratio.
        def concurrency_controller():
            return False

        # Let the test be a long running one so we can capture the scale-up and scale-down.
        config = {
            "handler": MagicMock(),
            "refresh_worker": False,
            "concurrency_controller": concurrency_controller,
            "rp_args": {
                "rp_debugger": True,
                "rp_log_level": "DEBUG"
            }
        }

        # Let's stop after the 20th call.
        scale_behavior = {
            'counter': 0
        }

        def mock_is_alive():
            res = scale_behavior['counter'] < 10
            scale_behavior['counter'] += 1

            # Let's oscillate between upscaling, downscaling, upscaling, downscaling, ...
            if scale_behavior['counter'] % 2 == 0:
                mock_get_job.return_value = {
                    "id": "123", "input": {"number": 1}}
            else:
                mock_get_job.return_value = None
            return res

        # Define the mock behaviors
        mock_run_job.return_value = {"output": {"result": "odd"}}
        with patch("runpod.serverless.modules.rp_scale.JobScaler.is_alive", wraps=mock_is_alive):
            runpod.serverless.start(config)

        # Assert that the mock_get_job, mock_run_job, and mock_send_result is called
        # 1 + 2 + 1 + 2 + 1 + 2 + 1 + 2 + 1 = 13 calls
        assert mock_get_job.call_count == 13

        # 5 calls with actual jobs
        assert mock_run_job.call_count == 5
        assert mock_send_result.call_count == 5
