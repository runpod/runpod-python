"""
Tests for the rp_progress.py module.
"""

import unittest
from unittest.mock import patch, Mock
from threading import Event

from runpod.serverless import progress_update

class TestProgressUpdate(unittest.TestCase):
    """ Tests for the progress_update function. """

    @patch("runpod.serverless.modules.rp_progress.os.environ.get")
    @patch("runpod.serverless.modules.rp_progress.aiohttp.ClientSession")
    @patch("runpod.serverless.modules.rp_progress.send_result")
    @patch("runpod.serverless.modules.rp_progress.threading.Thread")
    def test_progress_update(self, mock_thread, mock_result, mock_client_session, mock_os_get):
        """
        Tests that the progress_update function.
        """
        # Create an event to track thread completion
        thread_event = Event()

        def mock_start(self):
            self._target(*self._args, **self._kwargs)
            thread_event.set()  # Set the event after thread completion

        mock_thread.start = mock_start

        # Set mock values
        mock_os_get.return_value = "fake_api_key"
        fake_session = Mock()
        mock_client_session.return_value = fake_session

        # Call the function
        job = "fake_job"
        progress = "50%"
        progress_update(job, progress)

        # Wait for the "thread" to complete
        thread_event.wait()

        # Assertions
        mock_os_get.assert_called_once_with('RUNPOD_AI_API_KEY')
        mock_client_session.assert_called_once()

        expected_job_data = {
            "status": "IN_PROGRESS",
            "output": progress
        }
        mock_result.assert_called_once_with(fake_session, expected_job_data, job)
