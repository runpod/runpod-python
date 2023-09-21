"""
Tests for the rp_progress.py module.
"""

import unittest
from unittest.mock import patch
from threading import Event

from runpod.serverless import progress_update

class TestProgressUpdate(unittest.TestCase):
    """ Tests for the progress_update function. """

    @patch("runpod.serverless.modules.rp_progress.os.environ.get")
    @patch("runpod.serverless.modules.rp_progress.send_result")
    @patch("runpod.serverless.modules.rp_progress._thread_target")
    def test_progress_update(self, mock_thread_target, mock_result, mock_os_get):
        """
        Tests that the progress_update function.
        """
        # Create an event to track thread completion
        thread_event = Event()

        def mock_thread_function(job, progress):
            try:
                # You can insert logic or assertions here if you want
                pass
            except Exception as err:
                print(f"Exception in mocked function: {err}")
            finally:
                thread_event.set()

        mock_thread_target.side_effect = mock_thread_function

        # Set mock values
        mock_os_get.return_value = "fake_api_key"
        thread_event.clear()

        # Call the function
        job = "fake_job"
        progress = "50%"
        progress_update(job, progress)

        assert mock_thread_target.called, "Thread function was not started"
        mock_thread_target.assert_called_once_with(job, progress)
        assert thread_event.wait(timeout=10), "Thread did not complete within expected time"

        # Assertions
        mock_os_get.assert_called_once_with('RUNPOD_AI_API_KEY')
        expected_job_data = {
            "status": "IN_PROGRESS",
            "output": progress
        }
