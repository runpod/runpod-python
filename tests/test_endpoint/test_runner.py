import unittest
from unittest.mock import patch, Mock
import runpod

runpod.api_key = "YOUR_API_KEY"
endpoint = runpod.Endpoint("ENDPOINT_ID")


class TestEndpoint(unittest.TestCase):

    @patch('requests.post')
    def test_run(self, mock_post):
        # Configure the mock response for the requests.post() call
        mock_post.return_value = Mock()
        mock_post.return_value.json.return_value = {"id": "JOB_ID"}

        # Test that the run method returns a Job object
        job = endpoint.run({"YOUR_MODEL_INPUT_JSON": "YOUR_MODEL_INPUT_VALUE"})
        self.assertIsInstance(job, runpod.endpoint.Job)

    @patch('requests.post')
    def test_run_sync(self, mock_post):
        # Configure the mock response for the requests.post() call
        mock_post.return_value = Mock()
        mock_post.return_value.json.return_value = {"output": {
            "YOUR_MODEL_OUTPUT_JSON": "YOUR_MODEL_OUTPUT_VALUE"}}

        # Test that the run_sync method returns output within 90 seconds
        output = endpoint.run_sync({"YOUR_MODEL_INPUT_JSON": "YOUR_MODEL_INPUT_VALUE"})
        self.assertTrue(output is not None)


class TestJob(unittest.TestCase):

    @patch('requests.get')
    def setUp(self, mock_get):
        # Configure the mock response for the requests.get() call
        mock_get.return_value = Mock()
        mock_get.return_value.json.side_effect = [{"status": "PENDING"}, {
            "status": "COMPLETED", "output": {"YOUR_MODEL_OUTPUT_JSON": "YOUR_MODEL_OUTPUT_VALUE"}}]

        # Create a new job for testing
        self.job = runpod.endpoint.Job(endpoint_id="ENDPOINT_ID", job_id="JOB_ID")

    @patch('requests.get')
    def test_status(self, mock_get):
        # Test that the status method returns a string
        status = self.job.status()
        self.assertIsInstance(status, str)

    @patch('requests.get')
    def test_output(self, mock_get):
        # Test that the output method returns a dict
        output = self.job.output()
        self.assertIsInstance(output, dict)
