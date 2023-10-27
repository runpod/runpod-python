""" Unit tests for the runpodignore module in the cli.utils package. """

# pylint: disable=duplicate-code

import os
import unittest
from unittest.mock import patch, mock_open

from runpod.cli.utils.rp_runpodignore import get_ignore_list, should_ignore


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
            ".git/",
            "*.tmp",
            "*.log"
        ])
        assert mock_isfile.called

    @patch('os.path.isfile', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data="*.hello\n# This is a comment\n\n.world\n") # pylint: disable=line-too-long
    def test_with_ignore_file(self, mock_file, mock_isfile):
        """ Test that the default ignore list is returned when no ignore file is present. """
        result = get_ignore_list()
        expected_patterns = [
            "__pycache__/",
            "*.pyc",
            ".*.swp",
            ".git/",
            "*.tmp",
            "*.log",
            "*.hello",
            ".world"
        ]
        self.assertEqual(result, expected_patterns)
        assert mock_file.called
        assert mock_isfile.called

    @patch('os.path.isfile', return_value=True)
    @patch('builtins.open', new_callable=mock_open, read_data="# Only comments and empty lines\n\n\n") # pylint: disable=line-too-long
    def test_empty_ignore_file(self, mock_file, mock_isfile):
        """ Test that the default ignore list is returned when no ignore file is present. """
        result = get_ignore_list()
        self.assertEqual(result, [
            "__pycache__/",
            "*.pyc",
            ".*.swp",
            ".git/",
            "*.tmp",
            "*.log"
        ])
        assert mock_file.called
        assert mock_isfile.called

class TestShouldIgnoreFunction(unittest.TestCase):
    """ Unit tests for the should_ignore function. """

    def test_should_ignore_with_relative_path(self):
        """ Test that the should_ignore function returns True when the file should be ignored. """
        patterns = ["/test/"]
        file_path = os.path.join(os.getcwd(), "test", "example.txt")
        self.assertTrue(should_ignore(file_path, patterns))

    def test_should_ignore_with_absolute_path(self):
        """ Test that the should_ignore function returns True when the file should be ignored. """
        patterns = ["*.txt"]
        file_path = os.path.join(os.getcwd(), "test", "example.txt")
        self.assertTrue(should_ignore(file_path, patterns))

    def test_should_not_ignore(self):
        """ Test that the should_ignore
        Function returns False when the file should not be ignored
        """
        patterns = ["*.md"]
        file_path = os.path.join(os.getcwd(), "test", "example.txt")
        self.assertFalse(should_ignore(file_path, patterns))

    @patch("runpod.cli.utils.rp_runpodignore.get_ignore_list")
    def test_should_ignore_with_mocked_ignore_list(self, mock_get_ignore_list):
        """ Test that the should_ignore function returns True when the file should be ignored. """
        mock_get_ignore_list.return_value = ["*.txt"]
        file_path = "example.txt"
        self.assertTrue(should_ignore(file_path))

    @patch("runpod.cli.utils.rp_runpodignore.get_ignore_list")
    def test_should_not_ignore_with_mocked_ignore_list(self, mock_get_ignore_list):
        """ Test that the should_ignore
        Function returns False when the file should not be ignored
        """
        mock_get_ignore_list.return_value = ["*.md"]
        file_path = "example.txt"
        self.assertFalse(should_ignore(file_path))

    @patch("runpod.cli.utils.rp_runpodignore.os.getcwd")
    def test_should_ignore_with_mocked_cwd(self, mock_getcwd):
        """ Test that the should_ignore function returns True when the file should be ignored. """
        mock_getcwd.return_value = "/fake/directory"
        patterns = ["/test/"]
        file_path = "/fake/directory/test/example.txt"
        self.assertTrue(should_ignore(file_path, patterns))
