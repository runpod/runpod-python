"""
Tests for the rp_progress.py module.
"""

import unittest
from threading import Event
from unittest.mock import ANY, patch

from runpod.serverless.modules.rp_progress import _thread_target, progress_update


class TestProgressUpdate(unittest.TestCase):
    """Tests for the progress_update function."""

    @patch("runpod.serverless.modules.rp_progress.send_result")
    @patch("runpod.serverless.modules.rp_progress._thread_target")
    def test_progress_update(self, mock_thread_target, mock_result):
        """
        Tests that the progress_update function.
        """
        # Create an event to track thread completion
        thread_event = Event()

        def mock_thread_function(job, progress):
            try:
                assert job == "fake_job", "Job ID was not passed correctly"
                assert progress == "50%", "Progress was not passed correctly"
            except Exception as err:  # pylint: disable=broad-except
                print(f"Exception in mocked function: {err}")
            finally:
                thread_event.set()

        mock_thread_target.side_effect = mock_thread_function

        # Call the function
        job = {"id": "fake_job"}
        progress = "50%"
        progress_update(job, progress)
        _thread_target(job, progress)

        assert mock_thread_target.called, "Thread function was not started"
        mock_thread_target.assert_called_once_with(job, progress)
        assert thread_event.wait(
            timeout=30
        ), "Thread did not complete within expected time"

        # Assertions
        expected_job_data = {"status": "IN_PROGRESS", "output": progress}
        mock_result.assert_called_once_with(ANY, expected_job_data, job)
