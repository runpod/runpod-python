'''
Tests for runpod | endpoint | modules | endpoint.py
'''

import time
import unittest
from unittest.mock import patch, Mock, MagicMock
from itertools import cycle
import requests

import runpod
from runpod.endpoint import runner
from runpod.endpoint.runner import Endpoint, Job


class TestEndpoint(unittest.TestCase):
    ''' Tests for Endpoint '''

    ENDPOINT_ID = "ENDPOINT_ID"
    MOCK_API_KEY = "MOCK_API_KEY"
    MODEL_INPUT = {"YOUR_MODEL_INPUT_JSON": "YOUR_MODEL_INPUT_VALUE"}
    MODEL_OUTPUT = {"result": "YOUR_MODEL_OUTPUT_VALUE"}

    def setUp(self):
        ''' Common setup for the tests. '''
        runpod.api_key = self.MOCK_API_KEY
        self.endpoint = Endpoint(self.ENDPOINT_ID)

    @patch('runpod.endpoint.runner.RunPodClient._request')
    def test_endpoint_run(self, mock_client_request):
        ''' Test the run method of Endpoint with a successful job initiation. '''
        mock_client_request.return_value = {"id": "123", "status": "IN_PROGRESS"}

        run_request = self.endpoint.run(self.MODEL_INPUT)

        # Tests
        mock_client_request.assert_called_once_with(
            'POST', f"{self.ENDPOINT_ID}/run",
            {'input': {'YOUR_MODEL_INPUT_JSON': 'YOUR_MODEL_INPUT_VALUE'}}, 10
        )

        self.assertIsInstance(run_request, Job)
        self.assertEqual(run_request.job_id, "123")
        self.assertEqual(run_request.status(), "IN_PROGRESS")

        mock_client_request.assert_called_with(
            'GET', f"{self.ENDPOINT_ID}/status/123", timeout=10)

    @patch('runpod.endpoint.runner.RunPodClient._request')
    def test_endpoint_run_sync(self, mock_client_request):
        ''' Test the run_sync method of Endpoint with a successful job initiation. '''
        mock_client_request.return_value = {
            "id": "123", "status": "COMPLETED", "output": self.MODEL_OUTPUT}

        run_request = self.endpoint.run_sync(self.MODEL_INPUT)

        # Tests
        self.assertEqual(
            run_request, {"id": "123", "status": "COMPLETED", "output": self.MODEL_OUTPUT})

        mock_client_request.assert_called_once_with(
            'POST', f"{self.ENDPOINT_ID}/runsync",
            {'input': {'YOUR_MODEL_INPUT_JSON': 'YOUR_MODEL_INPUT_VALUE'}}, 90
        )

    def test_missing_api_key(self):
        '''
        Tests Endpoint.run without api_key
        '''
        with self.assertRaises(RuntimeError):
            runpod.api_key = None
            self.endpoint.run(self.MODEL_INPUT)

    @patch.object(requests.Session, 'post')
    def test_run_with_401(self, mock_post):
        '''
        Tests Endpoint.run with 401 status code
        '''
        mock_response = Mock()
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        endpoint = runpod.Endpoint("ENDPOINT_ID")
        request_data = {"YOUR_MODEL_INPUT_JSON": "YOUR_MODEL_INPUT_VALUE"}

        with self.assertRaises(RuntimeError):
            endpoint.run(request_data)

    @patch.object(runpod.endpoint.runner.RunPodClient, '_request')
    def test_run(self, mock_client_request):
        '''
        Tests Endpoint.run
        '''
        mock_client_request.return_value = {
            "id": "123",
            "status": "IN_PROGRESS"
        }

        runpod.api_key = "MOCK_API_KEY"
        endpoint = runpod.Endpoint("ENDPOINT_ID")

        request_data = {"YOUR_MODEL_INPUT_JSON": "YOUR_MODEL_INPUT_VALUE"}
        run_request = endpoint.run(request_data)

        self.assertEqual(run_request.job_id, "123")
        self.assertEqual(run_request.status(), "IN_PROGRESS")

    @patch.object(runpod.endpoint.runner.RunPodClient, '_request')
    def test_run_sync(self, mock_client_request):
        '''
        Tests Endpoint.run_sync
        '''
        mock_client_request.return_value = {
            "id": "123",
            "status": "COMPLETED",
            "output": {"result": "YOUR_MODEL_OUTPUT_VALUE"}
        }

        runpod.api_key = "MOCK_API_KEY"
        endpoint = runpod.Endpoint("ENDPOINT_ID")

        request_data = {"YOUR_MODEL_INPUT_JSON": "YOUR_MODEL_INPUT_VALUE"}
        run_request = endpoint.run_sync(request_data)

        self.assertEqual(run_request, {
            "id": "123",
            "status": "COMPLETED",
            "output": {"result": "YOUR_MODEL_OUTPUT_VALUE"}
        })


class TestJob(unittest.TestCase):
    ''' Tests for Job '''

    @patch('runpod.endpoint.runner.RunPodClient._request')
    @patch('runpod.endpoint.runner.RunPodClient')
    def test_status(self, mock_client, mock_client_request):
        '''
        Tests Job.status
        '''
        mock_client_request.return_value = {
            "status": "COMPLETED"
        }

        job = runner.Job("endpoint_id", "job_id", mock_client)
        status = job.status()
        self.assertEqual(status, "COMPLETED")

    @patch('runpod.endpoint.runner.RunPodClient._request')
    @patch('runpod.endpoint.runner.RunPodClient')
    def test_output(self, mock_client, mock_client_request):
        '''
        Tests Job.output
        '''
        mock_client_request.return_value = {
            "status": "COMPLETED",
            "output": "Job output"
        }

        mock_client._request.return_value = {  # pylint: disable=protected-access
            "status": "COMPLETED",
            "output": "Job output"
        }

        job = runner.Job("endpoint_id", "job_id", mock_client)
        output = job.output()
        self.assertEqual(output, "Job output")

    @patch.object(runner.RunPodClient, 'get')
    @patch.object(time, 'sleep', return_value=None)
    def test_output_with_sleep(self, mock_sleep, mock_get):
        '''
        Tests Job.output with sleep
        '''
        mock_response_1 = {
            "status": "IN_PROGRESS",
        }

        mock_response_2 = {
            "status": "COMPLETED",
            "output": "Job output"
        }

        # Set get().json() to return a different response depending on the call count
        mock_get.return_value.json.side_effect = cycle([mock_response_1, mock_response_2])

        job = runner.Job("endpoint_id", "job_id", Mock())
        output = job.output(timeout=0.2)

        self.assertEqual(output, None)
        self.assertEqual(mock_sleep.call_count, 1)

    @patch.object(runner.RunPodClient, 'get')
    def test_output_without_output_key(self, mock_get):
        '''
        Tests Job.output without output key
        '''
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "COMPLETED",
        }
        mock_get.return_value = mock_response

        job = runner.Job("endpoint_id", "job_id", Mock())
        output = job.output()
        self.assertIsNone(output)  # Check if output is None
