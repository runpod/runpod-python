""" Tests for runpod.serverless.modules.rp_tips.py """

import unittest
from unittest.mock import patch

from runpod.serverless.modules.rp_tips import check_return_size


class TestTips(unittest.TestCase):
    """Tests for the Tips module"""

    @patch("runpod.serverless.modules.rp_tips.log.tip")
    def test_check_return_size_small(self, mock_log):
        """
        Tests check_return_size function with a small return_body
        """
        check_return_size("a" * 10)  # A small string

        # Ensure that the log.tip function was not called, as the return_body is small
        mock_log.assert_not_called()

    @patch("runpod.serverless.modules.rp_tips.log.tip")
    def test_check_return_size_large(self, mock_log):
        """
        Tests check_return_size function with a large return_body
        """
        check_return_size("a" * 30_000_000)  # A large string (over 20 MB)

        # Ensure that the log.tip function was called, as the return_body is large
        mock_log.assert_called()
