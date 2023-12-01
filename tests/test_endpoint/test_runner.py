'''
Tests for runpod | endpoint | modules | endpoint.py
'''

import unittest
from unittest.mock import patch, Mock
import requests

import runpod
from runpod.endpoint import runner
from runpod.endpoint.runner import RunPodClient, Job, Endpoint


class TestRunPodClient(unittest.TestCase):
    """ Tests for RunPodClient """

    def test_no_api_key(self):
        '''
        Tests RunPodClient with no api_key
        '''
        with self.assertRaises(RuntimeError):
            runpod.api_key = None
            RunPodClient()

    @patch.object(requests.Session, 'post')
    def test_post_with_401(self, mock_post):
        '''
        Tests RunPodClient.post with 401 status code
        '''
        mock_response = Mock()
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        with self.assertRaises(RuntimeError):
            runpod.api_key = "MOCK_API_KEY"
            client = RunPodClient()
            client.post("ENDPOINT_ID/run", {"input": {}})

    @patch.object(requests.Session, 'request')
    def test_post(self, mock_post):
        '''
        Tests RunPodClient.post
        '''
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "123"}
        mock_post.return_value = mock_response

        runpod.api_key = "MOCK_API_KEY"
        client = RunPodClient()
        response = client.post("ENDPOINT_ID/run", {"input": {}})

        self.assertEqual(response, {"id": "123"})

    @patch.object(requests.Session, 'get')
    def test_get_with_401(self, mock_get):
        '''
        Tests RunPodClient.get with 401 status code
        '''
        mock_response = Mock()
        mock_response.status_code = 401
        mock_get.return_value = mock_response

        with self.assertRaises(RuntimeError):
            runpod.api_key = "MOCK_API_KEY"
            client = RunPodClient()
            client.get("ENDPOINT_ID/status/123")

    @patch.object(requests.Session, 'request')
    def test_get(self, mock_get):
        '''
        Tests RunPodClient.get
        '''
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "COMPLETED"}
        mock_get.return_value = mock_response

        runpod.api_key = "MOCK_API_KEY"
        client = RunPodClient()
        response = client.get("ENDPOINT_ID/status/123")

        self.assertEqual(response, {"status": "COMPLETED"})


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
        self.assertEqual(run_request, self.MODEL_OUTPUT)

        mock_client_request.assert_called_once_with(
            'POST', f"{self.ENDPOINT_ID}/runsync",
            {'input': {'YOUR_MODEL_INPUT_JSON': 'YOUR_MODEL_INPUT_VALUE'}}, 86400
        )

    @patch('runpod.endpoint.runner.RunPodClient._request')
    def test_endpoint_health(self, mock_client_request):
        ''' Test the health method of Endpoint '''
        self.endpoint.health()

        mock_client_request.assert_called_once_with('GET', f"{self.ENDPOINT_ID}/health", timeout=3)

    @patch('runpod.endpoint.runner.RunPodClient._request')
    def test_endpoint_purge_queue(self, mock_client_request):
        ''' Test the health method of Endpoint '''
        self.endpoint.purge_queue()

        mock_client_request.assert_called_once_with(
            'POST', f"{self.ENDPOINT_ID}/purge-queue",
            None, 3
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

        self.assertEqual(run_request, {"result": "YOUR_MODEL_OUTPUT_VALUE"})

    @patch.object(runpod.endpoint.runner.RunPodClient, '_request')
    def test_run_sync_with_timeout(self, mock_client_request):
        '''
        Tests Endpoint.run_sync with timeout
        '''
        mock_client_request.return_value = {
            "id": "123",
            "status": "IN_PROGRESS"
        }

        runpod.api_key = "MOCK_API_KEY"
        endpoint = runpod.Endpoint("ENDPOINT_ID")

        request_data = {"YOUR_MODEL_INPUT_JSON": "YOUR_MODEL_INPUT_VALUE"}
        with self.assertRaises(TimeoutError):
            endpoint.run_sync(request_data, timeout=1)


class TestJob(unittest.TestCase):
    ''' Tests for Job '''
    MODEL_OUTPUT = {"result": "YOUR_MODEL_OUTPUT_VALUE"}

    @patch('runpod.endpoint.runner.RunPodClient')
    def test_status(self, mock_client):
        '''
        Tests Job.status
        '''
        mock_client.get.return_value = {
            "status": "COMPLETED"
        }

        job = runner.Job("endpoint_id", "job_id", mock_client)
        status = job.status()
        self.assertEqual(status, "COMPLETED")

    @patch('runpod.endpoint.runner.RunPodClient')
    def test_output(self, mock_client):
        '''
        Tests Job.output
        '''
        mock_client.get.return_value = {
            "status": "COMPLETED",
            "output": "Job output"
        }

        job = runner.Job("endpoint_id", "job_id", mock_client)
        output = job.output()
        self.assertEqual(output, "Job output")

    @patch('runpod.endpoint.runner.RunPodClient')
    def test_output_with_sleep(self, mock_client):
        '''
        Tests Job.output with sleep
        '''
        mock_client.get.side_effect = [
            {
                "status": "IN_PROGRESS"
            },
            {
                "status": "COMPLETED",
                "output": "Job output"
            }
        ]

        job = runner.Job("endpoint_id", "job_id", mock_client)
        output = job.output(timeout=10)

        self.assertEqual(output, "Job output")

    @patch('runpod.endpoint.runner.RunPodClient')
    def test_output_timeout(self, mock_client):
        '''
        Tests Job.output with timeout
        '''
        mock_client.get.return_value = {
            "status": "IN_PROGRESS"
        }

        job = runner.Job("endpoint_id", "job_id", mock_client)
        with self.assertRaises(TimeoutError):
            job.output(timeout=1)

    @patch('runpod.endpoint.runner.RunPodClient')
    def test_cancel(self, mock_client):
        ''' Test the cancel method of Job with a successful job initiation. '''        
        job = runner.Job("endpoint_id", "job_id", mock_client)

        job.cancel()

        mock_client.post.assert_called_with("endpoint_id/cancel/job_id",
                                            data=None, timeout=3)


    @patch('runpod.endpoint.runner.RunPodClient')
    def test_job_status(self, mock_client):
        '''
        Tests Job.status
        '''
        mock_client.get.side_effect = [
            {
                "status": "IN_PROGRESS"
            },
            {
                "status": "COMPLETED"
            }
        ]

        job = runner.Job("endpoint_id", "job_id", mock_client)
        self.assertEqual(job.status(), "IN_PROGRESS")
        self.assertEqual(job.status(), "COMPLETED")
        self.assertEqual(job.status(), "COMPLETED")
