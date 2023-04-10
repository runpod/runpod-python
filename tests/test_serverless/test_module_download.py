''' Tests for runpod | serverless | modules | download.py '''
# pylint: disable=R0903,W0613

import os
import unittest
from unittest.mock import patch, mock_open

import requests

from runpod.serverless.utils.rp_download import download_files_from_urls

URL_LIST = ['https://example.com/picture.jpg',
            'https://example.com/picture.jpg?X-Amz-Signature=123']

JOB_ID = "job_123"


def mock_requests_get(*args, **kwargs):
    '''
    Mocks requests.get
    '''
    headers = {
        'Content-Disposition': 'attachment; filename="picture.jpg"'
    }

    class MockResponse:
        ''' Mocks requests.get response '''

        def __init__(self, content, status_code, headers=None):
            '''
            Mocks requests.get response
            '''
            self.content = content
            self.status_code = status_code
            self.headers = headers or {}

        def raise_for_status(self):
            ''' Mocks raise_for_status function '''
            if 400 <= self.status_code < 600:
                raise requests.exceptions.RequestException(f"Status code: {self.status_code}")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    if args[0] in URL_LIST:
        return MockResponse(b'nothing', 200, headers)

    return MockResponse(None, 404)


class TestDownloadFilesFromUrls(unittest.TestCase):
    ''' Tests for download_files_from_urls '''

    @patch('os.makedirs', return_value=None)
    @patch('requests.get', side_effect=mock_requests_get)
    @patch('builtins.open', new_callable=mock_open)
    def test_download_files_from_urls(self, mock_open_file, mock_get, mock_makedirs):
        '''
        Tests download_files_from_urls
        '''
        downloaded_files = download_files_from_urls(
            JOB_ID, ['https://example.com/picture.jpg', ]
        )

        self.assertEqual(len(downloaded_files), 1)

        # Check that the url was called with requests.get
        self.assertIn('https://example.com/picture.jpg', mock_get.call_args_list[0][0])

        mock_open_file.assert_called_once_with(downloaded_files[0], 'wb')
        mock_makedirs.assert_called_once_with(os.path.abspath(
            f'jobs/{JOB_ID}/downloaded_files'), exist_ok=True)

    @patch('os.makedirs', return_value=None)
    @patch('requests.get', side_effect=mock_requests_get)
    @patch('builtins.open', new_callable=mock_open)
    def test_download_files_from_urls_signed(self, mock_open_file, mock_get, mock_makedirs):
        '''
        Tests download_files_from_urls with signed urls
        '''
        downloaded_files = download_files_from_urls(
            JOB_ID, ['https://example.com/picture.jpg?X-Amz-Signature=123', ]
        )

        # Confirms that the same number of files were downloaded as urls provided
        self.assertEqual(len(downloaded_files), 1)

        # Check that the url was called with requests.get
        self.assertIn(URL_LIST[1], mock_get.call_args_list[0][0])

        mock_open_file.assert_called_once_with(downloaded_files[0], 'wb')
        mock_makedirs.assert_called_once_with(os.path.abspath(
            f'jobs/{JOB_ID}/downloaded_files'), exist_ok=True)
