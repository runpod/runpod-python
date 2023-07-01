''' Tests for runpod.serverless.modules.rp_logger '''

import unittest
from unittest.mock import patch

from runpod.serverless.modules import rp_logger


class TestLogger(unittest.TestCase):
    ''' Tests for rp_logger '''

    def test_default_log_level(self):
        '''
        Tests that the default log level is DEBUG
        '''
        logger = rp_logger.RunPodLogger()

        self.assertEqual(logger.level, "DEBUG")

    def test_singleton(self):
        '''
        Tests that the logger is a singleton
        '''
        logger1 = rp_logger.RunPodLogger()
        logger2 = rp_logger.RunPodLogger()

        self.assertIs(logger1, logger2)

    def test_set_log_level(self):
        '''
        Tests that the log level can be set
        '''
        logger = rp_logger.RunPodLogger()

        logger.set_level("INFO")
        self.assertEqual(logger.level, "INFO")

        logger.set_level("WARN")
        self.assertEqual(logger.level, "WARN")

    def test_call_log(self):
        '''
        Tests that the logger can be called
        '''
        log = rp_logger.RunPodLogger()

        with patch("runpod.serverless.modules.rp_logger.RunPodLogger.log") as mock_log:

            log.warn("Test log message")

            mock_log.assert_called_once_with("Test log message", "WARN")
