''' Tests for runpod | serverless| worker '''

import unittest
from unittest.mock import patch

import runpod



class TestWorker(unittest.TestCase):
    """ Tests for runpod | serverless| worker """

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_start(self):
        '''
        Test basic start call.
        '''

        with patch("runpod.serverless.worker.sys") as mock_exit, \
            self.assertRaises(SystemExit):
            runpod.serverless.start({})
            assert mock_exit.exit.called
