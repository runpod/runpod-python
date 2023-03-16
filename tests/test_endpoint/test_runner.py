'''
Unit tests for the endpoint module
'''

import unittest
from unittest.mock import patch, MagicMock
from runpod.endpoint import Endpoint, Job


class TestEndpoint(unittest.TestCase):
    ''' Tests the endpoint module.'''

    def setUp(self):
        '''
        Sets up the test environment.
        '''
        self.endpoint_id = None
        self.endpoint_url_base = 'https://api.runpod.ai/v1'
        self.api_key = 'my_api_key'

    def test_endpoint_run(self):
        '''
        Tests the endpoint run method.
        '''
        job_id = 'job_id_123'
        endpoint_input = {'input_1': 1, 'input_2': 2}
        mock_post = MagicMock(return_value=MagicMock(json=lambda: {'id': job_id}))
        with patch('requests.post', mock_post):
            endpoint = Endpoint(self.endpoint_id)
            job = endpoint.run(endpoint_input)
            mock_post.assert_called_once_with(
                f'{self.endpoint_url_base}/{self.endpoint_id}/run',
                headers={'Content-Type': 'application/json',
                         'Authorization': f'Bearer {self.api_key}'},
                json={'input': endpoint_input},
                timeout=10
            )
            self.assertEqual(job.endpoint_id, self.endpoint_id)
            self.assertEqual(job.job_id, job_id)

    def test_job_status(self):
        '''
        Tests the job status method.
        '''
        job_id = 'job_id_123'
        expected_status = 'COMPLETED'
        mock_get = MagicMock(return_value=MagicMock(json=lambda: {'status': expected_status}))
        with patch('requests.get', mock_get):
            job = Job(self.endpoint_id, job_id)
            status = job.status()
            mock_get.assert_called_once_with(
                f'{self.endpoint_url_base}/{self.endpoint_id}/status/{job_id}',
                headers={'Content-Type': 'application/json',
                         'Authorization': f'Bearer {self.api_key}'},
                timeout=10
            )
            self.assertEqual(status, expected_status)

    def test_job_output(self):
        '''
        Tests the job output method.
        '''
        job_id = 'job_id_123'
        expected_output = {'output_1': 1, 'output_2': 2}
        mock_get = MagicMock(return_value=MagicMock(json=lambda: {'output': expected_output}))
        with patch('requests.get', mock_get):
            job = Job(self.endpoint_id, job_id)
            output = job.output()
            mock_get.assert_called_once_with(
                f'{self.endpoint_url_base}/{self.endpoint_id}/status/{job_id}',
                headers={'Content-Type': 'application/json',
                         'Authorization': f'Bearer {self.api_key}'},
                timeout=10
            )
            self.assertEqual(output, expected_output)
