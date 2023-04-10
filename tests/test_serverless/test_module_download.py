''' Tests for runpod | serverless | modules | download.py '''
# pylint: disable=R0903,W0613

import unittest
from unittest.mock import patch, mock_open

from runpod.serverless.utils.rp_download import download_files_from_urls

URL_LIST = ['https://example.com/picture.jpg',
            'https://example.com/picture.jpg?X-Amz-Signature=123']

job_id = "job_123"


def mock_requests_get(*args, **kwargs):
    '''
    Mocks requests.get
    '''
    class MockResponse:
        ''' Mocks requests.get response '''

        def __init__(self, content, status_code):
            '''
            Mocks requests.get response
            '''
            self.content = content
            self.status_code = status_code

    if args[0] in URL_LIST:
        return MockResponse(b'nothing', 200)

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
            job_id, ['https://example.com/picture.jpg', ]
        )

        self.assertEqual(len(downloaded_files), 1)

        # Check that the url was called with requests.get
        self.assertIn('https://example.com/picture.jpg', mock_get.call_args_list[0][0])

        mock_open_file.assert_called_once_with(downloaded_files[0], 'wb')
        mock_makedirs.assert_called_once_with(f'jobs/{job_id}/downloaded_files', exist_ok=True)

    @patch('os.makedirs', return_value=None)
    @patch('requests.get', side_effect=mock_requests_get)
    @patch('builtins.open', new_callable=mock_open)
    def test_download_files_from_urls_signed(self, mock_open_file, mock_get, mock_makedirs):
        '''
        Tests download_files_from_urls with signed urls
        '''
        downloaded_files = download_files_from_urls(
            job_id, ['https://example.com/picture.jpg?X-Amz-Signature=123', ]
        )

        # Confirms that the same number of files were downloaded as urls provided
        self.assertEqual(len(downloaded_files), 1)

        # Check that the url was called with requests.get
        self.assertIn(URL_LIST[1], mock_get.call_args_list[0][0])

        mock_open_file.assert_called_once_with(downloaded_files[0], 'wb')
        mock_makedirs.assert_called_once_with(f'jobs/{job_id}/downloaded_files', exist_ok=True)
