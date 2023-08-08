''' Tests for runpod.serverless.modules.rp_ping '''

import os
import importlib

from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch, Mock

import requests
from runpod.serverless.modules import rp_ping


class MockResponse:
    ''' Mock response for requests '''
    status_code = 200

def mock_get(*args, **kwargs):
    '''
    Mock get function for requests
    '''
    return MockResponse()

class TestPing(TestCase):
    ''' Tests for rp_ping '''

    def setUp(self):
        os.environ["RUNPOD_WEBHOOK_PING"] = "https://test.com/ping"
        os.environ["RUNPOD_PING_INTERVAL"] = "20000"

    @patch("requests.Session.get", side_effect=mock_get)
    def test_start_ping(self):
        new_ping = rp_ping.Heartbeat()

        with patch("threading.Thread.start") as mock_thread_start:
            new_ping.start_ping()
            assert mock_thread_start.call_count == 1

        self.assertEqual(rp_ping.Heartbeat.PING_URL, "https://test.com/ping")

    def test_ping_url_not_set(self):
        os.environ["RUNPOD_WEBHOOK_PING"] = "PING_NOT_SET"
        importlib.reload(rp_ping)
        new_ping = rp_ping.Heartbeat()

        with patch("runpod.serverless.modules.rp_ping.log.error") as mock_log_error:
            new_ping.start_ping()
            assert mock_log_error.call_count == 1

    @patch("requests.Session.get", side_effect=mock_get)
    def test_send_ping(self):
        new_ping = rp_ping.Heartbeat()

        with patch("runpod.serverless.modules.rp_ping.jobs.get_job_list") as mock_job_list:
            mock_job_list.return_value = ["job1"]
            new_ping._send_ping()

    @patch("requests.Session.get", side_effect=requests.RequestException("Test Error"))
    def test_ping_exception(self, mock_get_return):
        new_ping = rp_ping.Heartbeat()

        with patch("runpod.serverless.modules.rp_ping.log.error") as mock_log_error:
            new_ping.ping_loop(test=True)
            assert mock_log_error.call_count == 1

    @patch("threading.Thread")
    def test_start_ping_multiple_calls(self, MockThread):
        new_ping = rp_ping.Heartbeat()
        new_ping.start_ping()
        new_ping.start_ping()

        # Verify that the thread is started only once
        self.assertEqual(MockThread.call_count, 1)

    @patch("threading.Thread")
    def test_daemon_thread_property(self, MockThread):
        new_ping = rp_ping.Heartbeat()
        new_ping.start_ping()

        # Verify that the thread is started as a daemon thread
        MockThread.assert_called_once_with(target=new_ping.ping_loop, name="ping_thread", daemon=True)

    @patch("time.sleep", side_effect=[None, KeyboardInterrupt])
    def test_sleep_interval(self, mock_sleep):
        new_ping = rp_ping.Heartbeat()
        try:
            new_ping.ping_loop()
        except KeyboardInterrupt:
            pass

        # Verify that sleep was called with the correct interval
        mock_sleep.assert_called_with(rp_ping.Heartbeat.PING_INTERVAL)

    def test_variables(self):
        os.environ["RUNPOD_WEBHOOK_PING"] = "https://test.com/ping"
        os.environ["RUNPOD_PING_INTERVAL"] = "20000"
        importlib.reload(rp_ping)

        self.assertEqual(rp_ping.Heartbeat.PING_URL, "https://test.com/ping")
        self.assertEqual(rp_ping.Heartbeat.PING_INTERVAL, 20)
