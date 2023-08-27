'''
Test shared functions related to authentication
'''
import unittest

from unittest.mock import patch, mock_open

class TestAPIKey(unittest.TestCase):

    # The mocked TOML file
    CREDENTIALS = """
    [default]
    api_key = "RUNPOD_API_KEY"
    """

    @patch('builtins.open', new_callable=mock_open, read_data=CREDENTIALS)
    def test_use_file_credentials(self, mock_file):
        '''
        Test that the API key is read from the credentials file
        '''
        from runpod import api_key
        self.assertEqual(api_key, "RUNPOD_API_KEY")
