''' Tests for runpod.serverless.modules.rp_logger '''

import unittest

from runpod.serverless.modules import rp_logger


class TestLogger(unittest.TestCase):
    ''' Tests for rp_logger '''

    def test_singleton(self):
        ''' Tests that the logger is a singleton '''
        logger1 = rp_logger.RunPodLogger()
        logger2 = rp_logger.RunPodLogger()

        self.assertIs(logger1, logger2)
