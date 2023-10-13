'''
Test shared functions related to authentication
'''

import unittest
import importlib

from unittest.mock import patch, mock_open

class TestAPIKey(unittest.TestCase):
    ''' Test the API key '''

    # The mocked TOML file
    CREDENTIALS = b"""
    [default]
    api_key = "RUNPOD_API_KEY"
    """

    @patch('builtins.open', new_callable=mock_open, read_data=CREDENTIALS)
    def test_use_file_credentials(self, mock_file):
        '''
        Test that the API key is read from the credentials file
        '''
        import runpod # pylint: disable=import-outside-toplevel
        importlib.reload(runpod)
        self.assertEqual(runpod.api_key, "RUNPOD_API_KEY")
        assert mock_file.called
