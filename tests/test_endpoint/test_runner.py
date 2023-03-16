'''
RunPod Test | Python | Endpoint Runner
'''

import unittest
from unittest.mock import patch
from runpod.endpoint import Endpoint, Job


class TestEndpoint(unittest.TestCase):
    ''' Tests the Endpoint class.'''

    def setUp(self):
        '''
        Sets up the test.
        '''
        self.endpoint_id = "my-endpoint-id"
        self.endpoint = Endpoint(self.endpoint_id)

    def test_endpoint_initialized_correctly(self):
        """Test that the Endpoint object is initialized correctly"""
        self.assertEqual(self.endpoint.endpoint_id, self.endpoint_id)
        self.assertEqual(self.endpoint.endpoint_url,
                         f"https://my-endpoint-url/{self.endpoint_id}/run")

    @patch('runpod.endpoint.requests.post')
    @patch('runpod.api_key', 'my-api-key')
    @patch('runpod.endpoint_url_base', 'https://my-endpoint-url')
    def test_run(self, mock_post):
        """Test the run method of the Endpoint object"""
        mock_post.return_value.json.return_value = {"id": "my-job-id"}

        job = self.endpoint.run("my-input")

        mock_post.assert_called_once_with(
            f"https://my-endpoint-url/{self.endpoint_id}/run",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer my-api-key"
            },
            json={"input": "my-input"},
            timeout=10
        )

        self.assertIsInstance(job, Job)
        self.assertEqual(job.endpoint_id, self.endpoint_id)
        self.assertEqual(job.job_id, "my-job-id")

    @patch('runpod.endpoint.time.sleep')
    @patch.object(Job, 'status', side_effect=["STARTED", "STARTED", "COMPLETED"])
    @patch('runpod.endpoint.requests.get')
    @patch('runpod.api_key', 'my-api-key')
    @patch('runpod.endpoint_url_base', 'https://my-endpoint-url')
    def test_output(self, mock_get, mock_status, mock_sleep):
        """Test the output method of the Job object"""
        mock_get.return_value.json.return_value = {"output": "my-output"}

        job = Job("my-endpoint-id", "my-job-id")

        output = job.output()

        mock_status.assert_called()
        mock_sleep.assert_called()
        mock_get.assert_called_once_with(
            "https://my-endpoint-url/my-endpoint-id/status/my-job-id",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer my-api-key"
            },
            timeout=10
        )

        self.assertEqual(output, "my-output")
