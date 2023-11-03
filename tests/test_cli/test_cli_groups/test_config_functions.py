'''
Unit tests for the config command.
'''

import unittest
from unittest.mock import patch, mock_open

from runpod.cli.groups.config import functions


class TestConfig(unittest.TestCase):
    ''' Unit tests for the config function. '''

    def setUp(self) -> None:
        self.sample_credentials = (
            '[default]\n'
            'api_key = "RUNPOD_API_KEY"\n'
        )

    @patch('os.path.exists', return_value=True)
    @patch('runpod.cli.groups.config.functions.toml.load')
    @patch('builtins.open', new_callable=mock_open, read_data='[default]\nkey = "value"')
    def test_get_credentials_existing_profile(self, mock_open_call, mock_toml_load, mock_exists):
        '''
        Tests the get_credentials function.
        '''
        mock_toml_load.return_value = {'default': {'key': 'value'}}
        result = functions.get_credentials('default')
        assert result == {'key': 'value'}
        assert mock_open_call.called
        assert mock_exists.called

    @patch('os.path.exists', return_value=True)
    @patch('runpod.cli.groups.config.functions.toml.load')
    @patch('builtins.open', new_callable=mock_open, read_data='[default]\nkey = "value"')
    def test_get_credentials_non_existent_profile(self, mock_open_call, mock_toml_load, mock_exists): # pylint: disable=line-too-long
        '''
        Tests the get_credentials function.
        '''
        mock_toml_load.return_value = {'default': {'key': 'value'}}
        result = functions.get_credentials('non_existent')
        assert result is None
        assert mock_open_call.called
        assert mock_exists.called
