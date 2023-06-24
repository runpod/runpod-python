'''
Tests for runpod | endpoint | modules | endpoint.py
'''

import unittest
from unittest.mock import patch, Mock
import requests

import runpod


class TestEndpoint(unittest.TestCase):
    ''' Tests for Endpoint '''

    def test_missing_api_key(self):
        '''
        Tests Endpoint.run without api_key
        '''
        with self.assertRaises(RuntimeError):
            runpod.Endpoint("ENDPOINT_ID")

    @patch.object(requests.Session, 'get')
    @patch.object(requests.Session, 'post')
    def test_run(self, mock_post, mock_get):
        '''
        Tests Endpoint.run
        '''
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "123",
            "status": "in_progress"
        }
        mock_post.return_value = mock_response
        mock_get.return_value = mock_response

        runpod.api_key = "MOCK_API_KEY"
        endpoint = runpod.Endpoint("ENDPOINT_ID")

        request_data = {"YOUR_MODEL_INPUT_JSON": "YOUR_MODEL_INPUT_VALUE"}
        run_request = endpoint.run(request_data)

        self.assertEqual(run_request.job_id, "123")
        self.assertEqual(run_request.status(), "in_progress")

    @patch.object(requests.Session, 'post')
    def test_run_sync(self, mock_post):
        '''
        Tests Endpoint.run_sync
        '''
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "123",
            "status": "completed",
            "output": {"result": "YOUR_MODEL_OUTPUT_VALUE"}
        }
        mock_post.return_value = mock_response

        runpod.api_key = "MOCK_API_KEY"
        endpoint = runpod.Endpoint("ENDPOINT_ID")

        request_data = {"YOUR_MODEL_INPUT_JSON": "YOUR_MODEL_INPUT_VALUE"}
        run_request = endpoint.run_sync(request_data)

        self.assertEqual(run_request, {
            "id": "123",
            "status": "completed",
            "output": {"result": "YOUR_MODEL_OUTPUT_VALUE"}
        })
