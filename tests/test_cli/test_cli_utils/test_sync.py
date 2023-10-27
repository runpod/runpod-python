"""Tests for runpod.cli.utils.rp_sync module."""

import unittest
from unittest.mock import patch, MagicMock
from runpod.cli.utils.rp_sync import WatcherHandler, sync_directory

class TestWatcherHandler(unittest.TestCase):
    """Tests for the WatcherHandler class."""

    @patch("runpod.cli.utils.rp_sync.should_ignore")
    def test_on_any_event_with_ignored_file(self, mock_should_ignore):
        """Test that the action function is not called when the file is ignored."""
        mock_should_ignore.return_value = True
        mock_action_function = MagicMock()
        handler = WatcherHandler(mock_action_function, "some_path")

        event_mock = MagicMock()
        event_mock.is_directory = False
        event_mock.src_path = "some_path/ignored_file.txt"

        handler.on_any_event(event_mock)
        mock_action_function.assert_not_called()

    @patch("runpod.cli.utils.rp_sync.should_ignore")
    def test_on_any_event_with_not_ignored_file(self, mock_should_ignore):
        """Test that the action function is called when the file is not ignored."""
        mock_should_ignore.return_value = False
        mock_action_function = MagicMock()
        handler = WatcherHandler(mock_action_function, "some_path")

        event_mock = MagicMock()
        event_mock.is_directory = False
        event_mock.src_path = "some_path/not_ignored_file.txt"

        handler.on_any_event(event_mock)
        mock_action_function.assert_called_once()

    def test_on_any_event_with_directory(self):
        """Test that the action function is not called when the event is a directory."""
        mock_action_function = MagicMock()
        handler = WatcherHandler(mock_action_function, "some_path")

        event_mock = MagicMock()
        event_mock.is_directory = True

        handler.on_any_event(event_mock)
        mock_action_function.assert_not_called()

class TestSyncDirectory(unittest.TestCase):
    """Tests for the sync_directory function."""

    @patch("runpod.cli.utils.rp_sync.threading.Thread.start", lambda x: None) # pylint: disable=unnecessary-lambda
    @patch("runpod.cli.utils.rp_sync.start_watcher")
    def test_sync_directory(self, mock_start_watcher):
        """Test that the sync_directory function calls the start_watcher function."""
        mock_ssh_client = MagicMock()

        local_path = "local_path"
        remote_path = "remote_path"

        sync_directory(mock_ssh_client, local_path, remote_path)

        mock_start_watcher.assert_called_once()
