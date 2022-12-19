''' Tests for runpod | serverless| modules | download.py '''
# pylint: disable=R0903,W0613

import unittest
from unittest.mock import patch, mock_open

from runpod.serverless.modules.download import download_input_objects


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

    if args[0] == 'https://example.com/picture.jpg':
        return MockResponse(b'nothing', 200)

    return MockResponse(None, 404)


class TestDownloadInputObjects(unittest.TestCase):
    ''' Tests for download_input_objects '''

    @patch('os.makedirs', return_value=None)
    @patch('requests.get', side_effect=mock_requests_get)
    @patch('builtins.open', new_callable=mock_open)
    def test_download_input_objects(self, mock_open_file, mock_get, mock_makedirs):
        '''
        Tests download_input_objects
        '''
        objects = download_input_objects(
            ['https://example.com/picture.jpg', ]
        )

        self.assertEqual(len(objects), 1)
        self.assertIn('https://example.com/picture.jpg', mock_get.call_args_list[0][0])
        mock_open_file.assert_called_once_with(objects[0], 'wb')
        mock_makedirs.assert_called_once_with('input_objects', exist_ok=True)
