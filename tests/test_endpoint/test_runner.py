import unittest
from unittest.mock import MagicMock, patch

import requests

from runpod import Endpoint
from runpod.endpoint.runner import Job


class TestEndpoint(unittest.TestCase):

    def setUp(self):
        self.endpoint_id = "test_endpoint"
        self.endpoint_url_base = "https://example.com/api"
        self.api_key = "test_api_key"
        self.endpoint_url = f"{self.endpoint_url_base}/{self.endpoint_id}/run"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

    def test_run(self):
        endpoint_input = {"input_1": 1, "input_2": 2}
        expected_job_id = "test_job_id"
        expected_response = {"id": expected_job_id}

        with patch.object(requests, "post") as mock_post:
            mock_post.return_value.json.return_value = expected_response
            endpoint = Endpoint(self.endpoint_id)

            job = endpoint.run(endpoint_input)

            mock_post.assert_called_once_with(
                self.endpoint_url,
                headers=self.headers,
                json={"input": endpoint_input},
                timeout=10,
            )
            self.assertEqual(job.endpoint_id, self.endpoint_id)
            self.assertEqual(job.job_id, expected_job_id)

    def test_run_sync(self):
        endpoint_input = {"input_1": 1, "input_2": 2}
        expected_response = {"status": "COMPLETED", "output": {"result": 42}}

        with patch.object(requests, "post") as mock_post:
            mock_post.return_value.json.return_value = expected_response
            endpoint = Endpoint(self.endpoint_id)

            response = endpoint.run_sync(endpoint_input)

            mock_post.assert_called_once_with(
                self.endpoint_url,
                headers=self.headers,
                json={"input": endpoint_input},
                timeout=100,
            )
            self.assertEqual(response, expected_response)


class TestJob(unittest.TestCase):

    def setUp(self):
        self.endpoint_id = "test_endpoint"
        self.job_id = "test_job_id"
        self.endpoint_url_base = "https://example.com/api"
        self.api_key = "test_api_key"
        self.status_url = f"{self.endpoint_url_base}/{self.endpoint_id}/status/{self.job_id}"
        self.output_url = f"{self.endpoint_url_base}/{self.endpoint_id}/output/{self.job_id}"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

    def test_status(self):
        expected_status = "COMPLETED"
        expected_response = {"status": expected_status}

        with patch.object(requests, "get") as mock_get:
            mock_get.return_value.json.return_value = expected_response
            job = Job(self.endpoint_id, self.job_id)

            status = job.status()

            mock_get.assert_called_once_with(
                self.status_url,
                headers=self.headers,
                timeout=10,
            )
            self.assertEqual(status, expected_status)

    def test_output(self):
        expected_output = {"result": 42}
        expected_response = {"output": expected_output}

        with patch.object(requests, "get") as mock_get:
            mock_get.return_value.json.return_value = expected_response
            job = Job(self.endpoint_id, self.job_id)

            output = job.output()

            mock_get.assert_called_once_with(
                self.output_url,
                headers=self.headers,
                timeout=10,
            )
            self.assertEqual(output, expected_output)
