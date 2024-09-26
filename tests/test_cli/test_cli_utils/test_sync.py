"""Tests for runpod.cli.utils.rp_sync module."""

import time
import unittest
from unittest.mock import ANY, MagicMock, patch

from runpod.cli import STOP_EVENT
from runpod.cli.utils.rp_sync import WatcherHandler, start_watcher, sync_directory


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
        handler.on_any_event(event_mock)  # Call it twice to test the debouncer
        time.sleep(2)
        mock_action_function.assert_called_once()

    def test_on_any_event_with_directory(self):
        """Test that the action function is not called when the event is a directory."""
        mock_action_function = MagicMock()
        handler = WatcherHandler(mock_action_function, "some_path")

        event_mock = MagicMock()
        event_mock.is_directory = True

        handler.on_any_event(event_mock)
        time.sleep(2)
        mock_action_function.assert_not_called()


class TestSyncDirectory(unittest.TestCase):
    """Tests for the sync_directory function."""

    @patch("runpod.cli.utils.rp_sync.threading.Thread")
    @patch("runpod.cli.utils.rp_sync.start_watcher")
    def test_sync_directory(self, mock_start_watcher, mock_thread_class):
        """Test that the sync_directory function calls the start_watcher function."""
        mock_ssh_client = MagicMock()

        local_path = "local_path"
        remote_path = "remote_path"

        sync_directory(mock_ssh_client, local_path, remote_path)

        target_function = mock_thread_class.call_args[1]["target"]
        target_function()

        mock_start_watcher.assert_called_once()

    @patch("runpod.cli.utils.rp_sync.threading.Thread")
    @patch("runpod.cli.utils.rp_sync.start_watcher")
    def test_sync_directory_sync_function(self, mock_start_watcher, mock_thread_class):
        """Test that the sync_directory function calls the start_watcher function."""
        mock_ssh_client = MagicMock()

        local_path = "local_path"
        remote_path = "remote_path"

        sync_function = sync_directory(mock_ssh_client, local_path, remote_path)
        sync_function()

        mock_ssh_client.rsync.assert_called_once_with(
            local_path, remote_path, quiet=True
        )

        mock_thread_class.assert_called_once()
        mock_thread_class.assert_called_with(
            target=mock_start_watcher, daemon=True, args=(ANY, local_path)
        )

        assert mock_start_watcher.called is False


class TestStartWatcher(unittest.TestCase):
    """Tests for the start_watcher function."""

    @patch("runpod.cli.utils.rp_sync.Observer")
    @patch("runpod.cli.utils.rp_sync.WatcherHandler")
    def test_start_watcher(self, mock_watch_handler, mock_observer_class):
        """Test that the start_watcher function starts the watcher correctly."""
        fake_action = MagicMock()
        local_path = "/path/to/watch"

        mock_observer_instance = mock_observer_class.return_value

        STOP_EVENT.clear()
        with patch("runpod.cli.utils.rp_sync.time.sleep") as mock_sleep:

            def side_effect(*args, **kwargs):
                del args, kwargs
                STOP_EVENT.set()

            mock_sleep.side_effect = side_effect
            start_watcher(fake_action, local_path)

        mock_watch_handler.assert_called_once_with(fake_action, local_path)

        mock_observer_instance.schedule.assert_called_once_with(
            mock_watch_handler.return_value, local_path, recursive=True
        )

        mock_observer_instance.start.assert_called_once()
        mock_observer_instance.stop.assert_called_once()
        mock_observer_instance.join.assert_called_once()
