''' Tests for runpod | serverless | modules | download.py '''
# pylint: disable=R0903,W0613

import os
import unittest
from unittest.mock import patch, mock_open, MagicMock

import requests

from runpod.serverless.utils.rp_download import(
    calculate_chunk_size, download_files_from_urls, file
)

URL_LIST = ['https://example.com/picture.jpg',
            'https://example.com/picture.jpg?X-Amz-Signature=123']

JOB_ID = "job_123"


def mock_requests_get(*args, **kwargs):
    '''
    Mocks requests.get
    '''
    headers = {
        'Content-Disposition': 'attachment; filename="picture.jpg"',
        'Content-Length': '1000'
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

        def iter_content(self, chunk_size=1024):
            ''' Mocks iter_content method '''
            length = len(self.content)
            for i in range(0, length, chunk_size):
                yield self.content[i:min(i + chunk_size, length)]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    if args[0] in URL_LIST:
        return MockResponse(b'nothing', 200, headers)

    return MockResponse(None, 404)


class TestDownloadFilesFromUrls(unittest.TestCase):
    ''' Tests for download_files_from_urls '''

    def test_calculate_chunk_size(self):
        '''
        Tests calculate_chunk_size
        '''
        self.assertEqual(calculate_chunk_size(1024), 1024)
        self.assertEqual(calculate_chunk_size(1024*1024), 1024)
        self.assertEqual(calculate_chunk_size(1024*1024*1024), 1024*1024)
        self.assertEqual(calculate_chunk_size(1024*1024*1024*10), 1024*1024*10)

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

        # Check that the file has the correct extension
        self.assertTrue(downloaded_files[0].endswith('.jpg'))

        mock_open_file.assert_called_once_with(downloaded_files[0], 'wb')
        mock_makedirs.assert_called_once_with(os.path.abspath(
            f'jobs/{JOB_ID}/downloaded_files'), exist_ok=True)


        string_download_file = download_files_from_urls(JOB_ID, 'https://example.com/picture.jpg')
        self.assertTrue(string_download_file[0].endswith('.jpg'))

        # Check if None is returned when url is None
        self.assertEqual(download_files_from_urls(JOB_ID, [None]), [None])

        # Test requests exception
        mock_get.side_effect = requests.exceptions.RequestException('Error')
        self.assertEqual(
            download_files_from_urls(JOB_ID, ['https://example.com/picture.jpg']),
            [None]
        )

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

        # Check that the file has the correct extension
        self.assertTrue(downloaded_files[0].endswith('.jpg'))

        mock_open_file.assert_called_once_with(downloaded_files[0], 'wb')
        mock_makedirs.assert_called_once_with(os.path.abspath(
            f'jobs/{JOB_ID}/downloaded_files'), exist_ok=True)

class FileDownloaderTestCase(unittest.TestCase):
    ''' Tests for file_downloader '''

    @patch('runpod.serverless.utils.rp_download.requests.get')
    @patch('builtins.open', new_callable=mock_open)
    def test_download_file(self, mock_file, mock_get):
        '''
        Tests download_file
        '''
        # Mock the response from requests.get
        mock_response = MagicMock()
        mock_response.content = b"file content"
        mock_response.headers = {"Content-Disposition": "filename=test_file.txt"}
        mock_get.return_value = mock_response

        # Call the function with a test URL
        result = file("http://test.com/test_file.txt")

        # Check the result
        self.assertEqual(result["type"], "txt")
        self.assertEqual(result["original_name"], "test_file.txt")
        self.assertTrue(result["file_path"].endswith(".txt"))
        self.assertIsNone(result["extracted_path"])

        # Check that the file was written correctly
        mock_file().write.assert_called_once_with(b"file content")

    @patch('runpod.serverless.utils.rp_download.requests.get')
    @patch('builtins.open', new_callable=mock_open)
    @patch('runpod.serverless.utils.rp_download.zipfile.ZipFile')
    def test_download_zip_file(self, mock_zip, mock_file, mock_get):
        '''
        Tests download_file with a zip file
        '''
        # Mock the response from requests.get
        mock_response = MagicMock()
        mock_response.content = b"zip file content"
        mock_response.headers = {"Content-Disposition": "filename=test_file.zip"}
        mock_get.return_value = mock_response

        # Call the function with a test URL
        result = file("http://test.com/test_file.zip")

        # Check the result
        self.assertEqual(result["type"], "zip")
        self.assertEqual(result["original_name"], "test_file.zip")
        self.assertTrue(result["file_path"].endswith(".zip"))
        self.assertIsNotNone(result["extracted_path"])

        # Check that the file was written correctly
        mock_file().write.assert_called_once_with(b"zip file content")

        # Check if no file name is provided
        mock_response.headers = {"Content-Disposition": ""}
        result = file("http://test.com/test_file.zip")
        self.assertEqual(result["original_name"], "test_file.zip")
