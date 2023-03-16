'''
Tests for runpod | endpoint | runner
'''

import unittest
from unittest.mock import patch
from requests.exceptions import Timeout

from runpod import api_key, endpoint_url_base
from runpod.endpoint.runner import Endpoint, Job


class TestEndpoint(unittest.TestCase):
    def setUp(self):
        self.endpoint_id = "test_endpoint"
        self.endpoint_url = f"{endpoint_url_base}/{self.endpoint_id}/run"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        self.endpoint = Endpoint(self.endpoint_id)

    @patch('runpod.runner.requests.post')
    def test_run(self, mock_post):
        mock_post.return_value.json.return_value = {"id": "test_job_id"}
        mock_post.return_value.status_code = 200

        endpoint_input = {"input": "test_input"}
        expected_job = Job(self.endpoint_id, "test_job_id")

        self.assertEqual(self.endpoint.run(endpoint_input), expected_job)
        mock_post.assert_called_once_with(
            self.endpoint_url, headers=self.headers, json=endpoint_input, timeout=10
        )

    @patch('runpod.runner.requests.post')
    def test_run_timeout(self, mock_post):
        mock_post.side_effect = Timeout

        endpoint_input = {"input": "test_input"}

        with self.assertRaises(Timeout):
            self.endpoint.run(endpoint_input)
        mock_post.assert_called_once_with(
            self.endpoint_url, headers=self.headers, json=endpoint_input, timeout=10
        )

    @patch('runpod.runner.requests.post')
    def test_run_sync(self, mock_post):
        mock_post.return_value.json.return_value = {"output": "test_output"}
        mock_post.return_value.status_code = 200

        endpoint_input = {"input": "test_input"}

        self.assertEqual(self.endpoint.run_sync(endpoint_input), {"output": "test_output"})
        mock_post.assert_called_once_with(
            self.endpoint_url, headers=self.headers, json=endpoint_input, timeout=100
        )

    @patch('runpod.runner.requests.get')
    def test_status(self, mock_get):
        mock_get.return_value.json.return_value = {"status": "COMPLETED"}
        mock_get.return_value.status_code = 200

        expected_status = "COMPLETED"
        test_job = Job(self.endpoint_id, "test_job_id")

        self.assertEqual(test_job.status(), expected_status)
        mock_get.assert_called_once_with(
            f"{endpoint_url_base}/{self.endpoint_id}/status/{test_job.job_id}",
            headers=self.headers, timeout=10
        )

    @patch('runpod.runner.time.sleep')
    @patch('runpod.runner.requests.get')
    def test_output(self, mock_get, mock_sleep):
        mock_get.return_value.json.side_effect = [
            {"status": "RUNNING"},
            {"status": "COMPLETED", "output": "test_output"}
        ]
        mock_get.return_value.status_code = 200

        expected_output = "test_output"
        test_job = Job(self.endpoint_id, "test_job_id")

        self.assertEqual(test_job.output(), expected_output)
        mock_sleep.assert_called_with(.1)
        mock_get.assert_has_calls([
            unittest.mock.call(
                f"{endpoint_url_base}/{self.endpoint_id}/status/{test_job.job_id}",
                headers=self.headers, timeout=10
            ),
            unittest.mock.call(
                f"{endpoint_url_base}/{self.endpoint_id}/status/{test_job.job_id}",
                headers=self.headers, timeout=10
            )
        ])
