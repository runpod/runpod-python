''' Tests for runpod.serverless.modules.rp_ping '''

import os
import importlib

import unittest
from unittest.mock import patch, Mock

import requests
from runpod.serverless.modules import rp_ping

class MockResponse: # pylint: disable=too-few-public-methods
    ''' Mock response for aiohttp '''
    status_code = 200

def mock_get(*args, **kwargs): # pylint: disable=unused-argument
    '''
    Mock get function for aiohttp
    '''
    return MockResponse()

class TestPing(unittest.TestCase):
    ''' Tests for rp_ping '''

    def test_variables(self):
        '''
        Tests that the variables are set correctly
        '''
        os.environ["RUNPOD_WEBHOOK_PING"] = "PING_NOT_SET"

        importlib.reload(rp_ping)

        self.assertEqual(rp_ping.Heartbeat.PING_URL, "PING_NOT_SET")
        self.assertEqual(rp_ping.Heartbeat.PING_INTERVAL, 10)

        os.environ["RUNPOD_WEBHOOK_PING"] = "https://test.com/ping"
        os.environ["RUNPOD_PING_INTERVAL"] = "20000"

        importlib.reload(rp_ping)

        self.assertEqual(rp_ping.Heartbeat.PING_URL, "https://test.com/ping")
        self.assertEqual(rp_ping.Heartbeat.PING_INTERVAL, 20)

    @patch("requests.Session.get", side_effect=mock_get)
    def test_start_ping(self, mock_get_return):
        '''
        Tests that the start_ping function works correctly
        '''
        os.environ["RUNPOD_WEBHOOK_PING"] = "https://test.com/ping"

        importlib.reload(rp_ping)
        new_ping = rp_ping.Heartbeat()

        mock_session = Mock()
        mock_session.headers.update = Mock()

        # Success case
        with patch("threading.Thread.start") as mock_thread_start:
            new_ping.start_ping(test=True)
            assert mock_thread_start.call_count == 1

        rp_ping.Heartbeat.PING_URL = "https://test.com/ping"
        new_ping.ping_loop(test=True)

        self.assertEqual(rp_ping.Heartbeat.PING_URL, "https://test.com/ping")

        # Exception case
        mock_get_return.side_effect = requests.RequestException("Test Error")

        with patch("runpod.serverless.modules.rp_ping.log.error") as mock_log_error:
            new_ping.ping_loop(test=True)
            assert mock_log_error.call_count == 1
