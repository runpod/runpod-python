'''
RunPod | Tests | CLI | Commands
'''

import unittest
from unittest.mock import patch
from click.testing import CliRunner

from runpod.cli.commands import runpod_cli

class TestCommands(unittest.TestCase):
    ''' A collection of tests for the CLI commands. '''

    def setUp(self):
        self.runner = CliRunner()

    def test_store_api_key(self):
        ''' Tests the store_api_key command. '''
        with patch('click.echo') as mock_echo, \
             patch('runpod.cli.commands.set_credentials') as mock_set_credentials:

            # Successful Call
            result = self.runner.invoke(runpod_cli, ['store_api_key', '--profile', 'test', 'KEY'])
            assert result.exit_code == 0
            assert mock_set_credentials.called_with('API_KEY_1234', 'test')
            assert mock_echo.call_count == 1

            # Unsuccessful Call
            mock_set_credentials.side_effect = ValueError()
            result = self.runner.invoke(runpod_cli, ['store_api_key', '--profile', 'test', 'KEY'])
            assert result.exit_code == 1

    def test_validate_credentials_file(self):
        ''' Tests the check_creds command. '''
        with patch('click.echo') as mock_echo, \
             patch('runpod.cli.commands.check_credentials') as mock_check_credentials:

            # Successful Validation
            mock_check_credentials.return_value = (True, None)
            result = self.runner.invoke(runpod_cli, ['check_creds', '--profile', 'test_pass'])
            assert mock_check_credentials.called_with('test_pass')
            assert result.exit_code == 0

            # Unsuccessful Validation
            mock_check_credentials.return_value = (False, 'Error')
            result = self.runner.invoke(runpod_cli, ['check_creds', '--profile', 'test'])
            assert result.exit_code == 1
            assert mock_check_credentials.called_with('test')
            assert mock_echo.call_count == 4
