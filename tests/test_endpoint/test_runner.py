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


class TestEndpoint(unittest.TestCase):
    ''' Tests for Endpoint '''

    def test_missing_api_key(self):
        '''
        Tests Endpoint.run without api_key
        '''
        with self.assertRaises(RuntimeError):
            runpod.Endpoint("ENDPOINT_ID")

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

    @patch.object(runner.RunPodClient, 'get')
    def test_status(self, mock_get):
        '''
        Tests Job.status
        '''
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "COMPLETED"
        }
        mock_get.return_value = mock_response

        job = runner.Job("endpoint_id", "job_id", Mock())
        status = job.status()
        self.assertEqual(status, "COMPLETED")

    @patch.object(runner.RunPodClient, 'get')
    def test_output(self, mock_get):
        '''
        Tests Job.output
        '''
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "COMPLETED",
            "output": "Job output"
        }
        mock_get.return_value = mock_response

        job = runner.Job("endpoint_id", "job_id", Mock())
        output = job.output()
        self.assertEqual(output, "Job output")

    @patch.object(runpod.endpoint.runner.RunPodClient, '_request')
    def test_error_status(self, mock_client_request):
        '''
        Tests Job.status with error status
        '''
        mock_client_request.side_effect = RuntimeError("Error")

        job = runner.Job("endpoint_id", "job_id", mock_client_request)
        with self.assertRaises(RuntimeError):
            job.status()

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
