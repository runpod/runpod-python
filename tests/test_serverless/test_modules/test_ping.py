""" Tests for runpod.serverless.modules.rp_ping """

import importlib
import os
import unittest
from unittest.mock import patch, MagicMock

import requests

from runpod.serverless.modules import rp_ping
from runpod.serverless.modules.rp_ping import Heartbeat
from runpod.serverless.modules.worker_state import JobsProgress


class MockResponse:
    """Mock response for aiohttp"""
    url = ""
    status_code = 200


def mock_get(*args, **kwargs):
    """
    Mock get function for aiohttp
    """
    return MockResponse()


class TestPing(unittest.TestCase):
    """Tests for rp_ping"""

    def test_default_variables(self):
        """
        Tests that the variables are set with default values
        """
        heartbeat = Heartbeat()
        assert heartbeat.PING_URL == "PING_NOT_SET"
        assert heartbeat.PING_INTERVAL == 10

    @patch.dict(os.environ, {"RUNPOD_WEBHOOK_PING": "https://test.com/ping"})
    @patch.dict(os.environ, {"RUNPOD_PING_INTERVAL": "1000"})
    def test_variables(self):
        """
        Tests that the variables are set correctly
        """
        importlib.reload(rp_ping)

        heartbeat = Heartbeat()
        assert heartbeat.PING_URL == "https://test.com/ping"
        assert heartbeat.PING_INTERVAL == 1

    @patch.dict(os.environ, {"RUNPOD_PING_INTERVAL": "1000"})
    @patch(
        "runpod.serverless.modules.rp_ping.SyncClientSession.get", side_effect=mock_get
    )
    def test_start_ping(self, mock_get_return):
        """
        Tests that the start_ping function works correctly
        """
        # No RUNPOD_AI_API_KEY case
        with patch("threading.Thread.start") as mock_thread_start:
            rp_ping.Heartbeat().start_ping(test=True)
            assert mock_thread_start.call_count == 0

        os.environ["RUNPOD_AI_API_KEY"] = "test_key"

        # No RUNPOD_POD_ID case
        with patch("threading.Thread.start") as mock_thread_start:
            rp_ping.Heartbeat().start_ping(test=True)
            assert mock_thread_start.call_count == 0

        os.environ["RUNPOD_POD_ID"] = "test_pod_id"

        # No RUNPOD_WEBHOOK_PING case
        with patch("threading.Thread.start") as mock_thread_start:
            rp_ping.Heartbeat().start_ping(test=True)
            assert mock_thread_start.call_count == 0

        os.environ["RUNPOD_WEBHOOK_PING"] = "https://test.com/ping"

        importlib.reload(rp_ping)

        # Success case
        with patch("threading.Thread.start") as mock_thread_start:
            rp_ping.Heartbeat().start_ping(test=True)
            assert mock_thread_start.call_count == 1

        rp_ping.Heartbeat.PING_URL = "https://test.com/ping"
        rp_ping.Heartbeat().ping_loop(test=True)

        self.assertEqual(rp_ping.Heartbeat.PING_URL, "https://test.com/ping")

        # Exception case
        mock_get_return.side_effect = requests.RequestException("Test Error")

        with patch("runpod.serverless.modules.rp_ping.log.error") as mock_log_error:
            rp_ping.Heartbeat().ping_loop(test=True)
            assert mock_log_error.call_count == 1


@patch.dict(os.environ, {"RUNPOD_PING_INTERVAL": "1000"})
class TestHeartbeat(unittest.IsolatedAsyncioTestCase):

    @patch.dict(os.environ, {"RUNPOD_AI_API_KEY": ""})
    @patch("runpod.serverless.modules.rp_ping.log")
    def test_start_ping_no_api_key(self, mock_logger):
        """Test start_ping method when RUNPOD_AI_API_KEY is missing."""
        heartbeat = Heartbeat()
        heartbeat.start_ping()
        mock_logger.debug.assert_called_once_with(
            "Not deployed on RunPod serverless, pings will not be sent."
        )

    @patch.dict(os.environ, {"RUNPOD_POD_ID": ""})
    @patch("runpod.serverless.modules.rp_ping.log")
    def _test_start_ping_no_pod_id(self, mock_logger):
        """Test start_ping method when RUNPOD_POD_ID is missing."""
        heartbeat = Heartbeat()
        heartbeat.start_ping()
        mock_logger.info.assert_called_once_with(
            "Not running on RunPod, pings will not be sent."
        )

    @patch("runpod.serverless.modules.rp_ping.Heartbeat._send_ping")
    def test_ping_loop(self, mock_send_ping):
        """Test ping_loop runs and exits correctly in test mode."""
        heartbeat = rp_ping.Heartbeat()
        heartbeat.ping_loop(test=True)
        mock_send_ping.assert_called_once()

    @patch("runpod.serverless.modules.rp_ping.SyncClientSession.get")
    async def test_send_ping(self, mock_get):
        """Test _send_ping method sends the correct request."""
        mock_response = MagicMock()
        mock_response.url = "http://localhost/ping"
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        jobs = JobsProgress()
        await jobs.add("job1")
        await jobs.add("job2")

        heartbeat = Heartbeat()
        heartbeat._send_ping()

        mock_get.assert_called_once()

        # Extract the arguments passed to the mock_get call
        _, kwargs = mock_get.call_args

        # Check that job_id is correct in params, ignoring other params
        assert 'params' in kwargs
        assert 'job_id' in kwargs['params']
        assert kwargs['params']['job_id'] in ["job1,job2", "job2,job1"]

    @patch("runpod.serverless.modules.rp_ping.log")
    def test_send_ping_exception(self, mock_logger):
        """Test _send_ping logs an error on exception."""
        heartbeat = Heartbeat()

        with patch.object(
            heartbeat._session,
            "get",
            side_effect=requests.RequestException("Error"),
        ):
            heartbeat._send_ping()

            mock_logger.error.assert_called_once_with(
                "Ping Request Error: Error, attempting to restart ping."
            )
