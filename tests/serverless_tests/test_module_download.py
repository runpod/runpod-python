''' Tests for runpod | serverless| modules | download.py '''

import unittest
from unittest import mock
from unittest.mock import patch, MagicMock, mock_open

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

    @patch('requests.get', side_effect=mock_requests_get)
    @patch('builtins.open', new_callable=mock_open)
    def test_download_input_objects(self):
        '''
        Tests download_input_objects
        '''
        objects = download_input_objects(
            ['https://example.com/picture.jpg', ]
        )
