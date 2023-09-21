"""
Tests for the rp_progress.py module.
"""


import unittest
from unittest.mock import patch, Mock
from runpod.serverless import progress_update

class TestProgressUpdate(unittest.IsolatedAsyncioTestCase):
    """ Tests for the progress_update function. """

    @patch("runpod.serverless.modules.rp_progress.os.environ.get")
    @patch("runpod.serverless.modules.rp_progress.aiohttp.ClientSession")
    @patch("runpod.serverless.modules.rp_progress.send_result")
    async def test_progress_update(self, mock_send_result, mock_client_session, mock_os_get):
        """
        Tests that the progress_update function calls the send_result function with the correct
        """
        # Set mock values
        mock_os_get.return_value = "fake_api_key"
        fake_session = Mock()
        mock_client_session.return_value = fake_session

        # Call the function
        job = "fake_job"
        progress = "50%"
        progress_update(job, progress)

        # Assertions
        mock_os_get.assert_called_once_with('RUNPOD_AI_API_KEY')
        mock_client_session.assert_called_once()

        expected_job_data = {
            "status": "IN_PROGRESS",
            "output": progress
        }
        mock_send_result.assert_called_once_with(fake_session, expected_job_data, job)
