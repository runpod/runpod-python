''' Tests for runpod | serverless | modules | endpoint.py '''
# pylint: disable=R0903,W0613

import unittest
from unittest.mock import patch, MagicMock
import runpod


class TestEndpoint(unittest.TestCase):
    ''' Tests for Endpoint '''

    @patch('runpod.endpoint.runner.requests.post')
    def test_run(self, mock_post):
        '''
        Tests Endpoint.run
        '''
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "123",
            "status": "in_progress"
        }
        mock_post.return_value = mock_response

        endpoint = runpod.Endpoint("ENDPOINT_ID")

        request_data = {"YOUR_MODEL_INPUT_JSON": "YOUR_MODEL_INPUT_VALUE"}
        run_request = endpoint.run(request_data)

        print("Response status code:", mock_response.status_code)
        print("Response text:", mock_response.text)
        print("JSON data:", mock_response.json())

        self.assertEqual(run_request.job_id, "123")
        self.assertEqual(run_request.status(), "in_progress")

    @patch('runpod.endpoint.runner.requests.post')
    def test_run_sync(self, mock_post):
        '''
        Tests Endpoint.run_sync
        '''
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": "123",
            "status": "completed",
            "output": {"result": "YOUR_MODEL_OUTPUT_VALUE"}
        }
        mock_post.return_value = mock_response

        endpoint = runpod.Endpoint("ENDPOINT_ID")

        request_data = {"YOUR_MODEL_INPUT_JSON": "YOUR_MODEL_INPUT_VALUE"}
        run_request = endpoint.run_sync(request_data)

        self.assertEqual(run_request, {"result": "YOUR_MODEL_OUTPUT_VALUE"})
