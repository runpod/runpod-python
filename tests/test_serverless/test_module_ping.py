''' Tests for runpod.serverless.modules.rp_ping '''

import os
import importlib

import unittest

from runpod.serverless.modules import rp_ping

class TestPing(unittest.TestCase):
    ''' Tests for rp_ping '''

    def test_variables(self):
        '''
        Tests that the variables are set correctly
        '''
        self.assertEqual(rp_ping.PING_URL, "PING_NOT_SET")
        self.assertEqual(rp_ping.PING_INTERVAL, 10000)

        os.environ["RUNPOD_WEBHOOK_PING"] = "https://test.com/ping"
        os.environ["RUNPOD_PING_INTERVAL"] = "20000"

        importlib.reload(rp_ping)

        self.assertEqual(rp_ping.PING_URL, "https://test.com/ping")
        self.assertEqual(rp_ping.PING_INTERVAL, 20000)
