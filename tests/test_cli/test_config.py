'''
Unit tests for the config command.
'''

import unittest
from unittest.mock import patch, mock_open

import runpod
from runpod.cli import config


class TestConfig(unittest.TestCase):
    ''' Unit tests for the config function. '''

    def setUp(self) -> None:
        self.sample_credentials = (
            '[default]\n'
            'api_key = "RUNPOD_API_KEY"\n'
        )

    @patch('builtins.open',  new_callable=mock_open())
    def test_set_credentials(self, mock_file):
        '''
        Tests the set_credentials function.
        '''
        config.set_credentials('RUNPOD_API_KEY')

        assert mock_file.called
        mock_file.assert_called_with(config.CREDENTIAL_FILE, 'w', encoding="UTF-8")


    @patch('runpod.cli.config.toml.load')
    @patch('runpod.cli.config.os.path.exists')
    def test_check_credentials(self, mock_exists, mock_toml_load):
        '''
        Tests the check_credentials function.
        '''
        mock_exists.return_value = False

        passed = runpod.check_credentials()
        assert passed is False

        mock_exists.return_value = True
        mock_toml_load.return_value = ""

        passed = runpod.check_credentials()
        assert passed is False

        mock_exists.return_value = True
        mock_toml_load.return_value = dict({'default': 'something'})

        passed = runpod.check_credentials()
        assert passed is False

        mock_toml_load.return_value = ValueError

        passed = runpod.check_credentials()
        assert passed is False

        mock_toml_load.return_value = dict({'default': 'api_key'})

        passed = runpod.check_credentials()
        assert passed is True
