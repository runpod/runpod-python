""" Unit tests for the runpodignore module in the cli.utils package. """

import unittest
from unittest.mock import patch, mock_open

from runpod.cli.utils.rp_runpodignore import get_ignore_list


class TestGetIgnoreList(unittest.TestCase):
    """ Unit tests for the get_ignore_list function. """

    @patch('os.path.isfile', return_value=False)
    def test_no_ignore_file(self, mock_isfile):
        """ Test that the default ignore list is returned when no ignore file is present. """
        result = get_ignore_list()
        self.assertEqual(result, [
            "__pycache__/",
            "*.pyc",
            ".*.swp",
            ".git/"
        ])
        assert mock_isfile.called

    @patch('os.path.isfile', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data="*.log\n# This is a comment\n\n.tmp\n")
    def test_with_ignore_file(self, mock_file, mock_isfile):
        """ Test that the default ignore list is returned when no ignore file is present. """
        result = get_ignore_list()
        expected_patterns = [
            "__pycache__/",
            "*.pyc",
            ".*.swp",
            ".git/",
            "*.log",
            ".tmp"
        ]
        self.assertEqual(result, expected_patterns)
        assert mock_file.called
        assert mock_isfile.called

    @patch('os.path.isfile', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data="# Only comments and empty lines\n\n\n")
    def test_empty_ignore_file(self, mock_file, mock_isfile):
        """ Test that the default ignore list is returned when no ignore file is present. """
        result = get_ignore_list()
        self.assertEqual(result, [
            "__pycache__/",
            "*.pyc",
            ".*.swp",
            ".git/"
        ])
        assert mock_file.called
        assert mock_isfile.called
