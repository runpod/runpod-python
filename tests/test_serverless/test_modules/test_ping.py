""" Tests for runpod.serverless.modules.rp_ping """

import importlib
import os
import unittest
from unittest.mock import patch, MagicMock

import requests

from runpod.serverless.modules import rp_ping
from runpod.serverless.modules.rp_ping import Heartbeat
from runpod.serverless.modules.worker_state import JobsProgress


class MockResponse:  # pylint: disable=too-few-public-methods
    """Mock response for aiohttp"""
    url = ""
    status_code = 200


def mock_get(*args, **kwargs):  # pylint: disable=unused-argument
    """
    Mock get function for aiohttp
    """
    return MockResponse()


class TestPing(unittest.TestCase):
    """Tests for rp_ping"""

    def test_variables(self):
        """
        Tests that the variables are set correctly
        """
        os.environ["RUNPOD_WEBHOOK_PING"] = "PING_NOT_SET"

        importlib.reload(rp_ping)

        self.assertEqual(rp_ping.Heartbeat.PING_URL, "PING_NOT_SET")
        self.assertEqual(rp_ping.Heartbeat.PING_INTERVAL, 10)

        os.environ["RUNPOD_WEBHOOK_PING"] = "https://test.com/ping"
        os.environ["RUNPOD_PING_INTERVAL"] = "20000"

        importlib.reload(rp_ping)

        self.assertEqual(rp_ping.Heartbeat.PING_URL, "https://test.com/ping")
        self.assertEqual(rp_ping.Heartbeat.PING_INTERVAL, 20)

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


class TestHeartbeat(unittest.TestCase):

    @patch("runpod.serverless.modules.rp_ping.SyncClientSession")
    @patch("runpod.serverless.modules.rp_ping.log")
    @patch("runpod.serverless.modules.rp_ping.Retry")
    def setUp(self, mock_retry, mock_logger, mock_session):
        # Mock environment variables
        os.environ["RUNPOD_AI_API_KEY"] = "test_api_key"
        os.environ["RUNPOD_POD_ID"] = "test_pod_id"
        os.environ["RUNPOD_WEBHOOK_PING"] = "http://localhost/ping/$RUNPOD_POD_ID"
        os.environ["RUNPOD_PING_INTERVAL"] = "10000"

        # Mock instances
        self.mock_logger = mock_logger
        self.mock_session = mock_session.return_value

    @patch.dict(os.environ, {"RUNPOD_AI_API_KEY": ""})
    @patch("runpod.serverless.modules.rp_ping.log")
    def test_start_ping_no_api_key(self, mock_logger):
        """Test start_ping method when RUNPOD_AI_API_KEY is missing."""
        heartbeat = Heartbeat()
        heartbeat.start_ping()
        mock_logger.debug.assert_called_once_with(
            "Not deployed on RunPod serverless, pings will not be sent."
        )

    @patch("runpod.serverless.modules.rp_ping.log")
    @patch.dict(os.environ, {"RUNPOD_POD_ID": ""})
    def test_start_ping_no_pod_id(self, mock_logger):
        """Test start_ping method when RUNPOD_POD_ID is missing."""
        heartbeat = Heartbeat()
        heartbeat.start_ping()
        mock_logger.info.assert_called_once_with(
            "Not running on RunPod, pings will not be sent."
        )

    @patch("runpod.serverless.modules.rp_ping.threading.Thread")
    def test_start_ping_thread_started(self, mock_thread):
        """Test that the ping thread is started only once."""
        heartbeat = Heartbeat()
        heartbeat._thread_started = False  # Reset thread flag for testing
        assert not Heartbeat._thread_started

        heartbeat.start_ping()
        assert Heartbeat._thread_started

        mock_thread.assert_called_once()

    @patch("runpod.serverless.modules.rp_ping.Heartbeat._send_ping")
    @patch.dict(os.environ, {"RUNPOD_PING_INTERVAL": "0"})
    def test_ping_loop(self, mock_send_ping):
        """Test ping_loop runs and exits correctly in test mode."""
        heartbeat = Heartbeat()
        heartbeat.ping_loop(test=True)
        mock_send_ping.assert_called_once()

