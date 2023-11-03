'''
RunPod | Tests | CLI | Commands
'''

import unittest
from unittest.mock import patch
from click.testing import CliRunner

from runpod.cli.entry import runpod_cli

class TestCommands(unittest.TestCase):
    ''' A collection of tests for the CLI commands. '''

    def setUp(self):
        self.runner = CliRunner()

    def test_config_wizard(self):
        ''' Tests the config command. '''
        with patch('click.echo') as mock_echo, \
            patch('runpod.cli.groups.config.commands.set_credentials') as mock_set_credentials, \
            patch('runpod.cli.groups.config.commands.check_credentials') as mock_check_creds, \
            patch('click.confirm', return_value=True) as mock_confirm, \
            patch('click.prompt', return_value='KEY') as mock_prompt:

            # Assuming credentials aren't set (doesn't prompt for overwrite)
            mock_check_creds.return_value = (False, None)

            # Successful Call with Direct Key
            result = self.runner.invoke(runpod_cli, ['config', '--profile', 'test', 'KEY'])
            assert result.exit_code == 0
            mock_set_credentials.assert_called_with('KEY', 'test', overwrite=True)
            assert mock_echo.call_count == 1

            # Successful Call with Prompted Key (since direct key isn't provided)
            result = self.runner.invoke(runpod_cli, ['config', '--profile', 'test'])
            assert result.exit_code == 0
            mock_set_credentials.assert_called_with('KEY', 'test', overwrite=True)
            mock_prompt.assert_called_with('    > RunPod API Key', hide_input=False, confirmation_prompt=False) # pylint: disable=line-too-long

            # Simulating existing credentials, prompting for overwrite
            mock_check_creds.return_value = (True, None)
            result = self.runner.invoke(runpod_cli, ['config', '--profile', 'test'])
            mock_confirm.assert_called_with(
                'Credentials already set for profile: test. Overwrite?', abort=True)

            # Unsuccessful Call
            mock_set_credentials.side_effect = ValueError()
            result = self.runner.invoke(runpod_cli, ['config', '--profile', 'test', 'KEY'])
            assert result.exit_code == 1

    def test_check_flag(self):
        """ Tests the --check flag. """
        with patch('runpod.cli.groups.config.commands.check_credentials') as mock_check_creds:
            # Assuming credentials are set
            mock_check_creds.return_value = (True, None)
            result = self.runner.invoke(runpod_cli, ['config', '--check', '--profile', 'test'])
            assert result.exit_code == 0

            # Assuming credentials aren't set
            mock_check_creds.return_value = (False, "Credentials not found.")
            result = self.runner.invoke(runpod_cli, ['config', '--check', '--profile', 'test'])
            assert result.exit_code == 1

    def test_output_messages(self):
        """ Tests the output messages for the config command. """
        with patch('click.echo') as mock_echo, \
            patch('runpod.cli.groups.config.commands.set_credentials') as mock_set_credentials, \
            patch('runpod.cli.groups.config.commands.check_credentials', return_value=(False, None)) as mock_check_creds: # pylint: disable=line-too-long
            result = self.runner.invoke(runpod_cli, ['config', 'KEY', '--profile', 'test'])
            mock_set_credentials.assert_called_with('KEY', 'test', overwrite=True)
            mock_echo.assert_any_call('Credentials set for profile: test in ~/.runpod/config.toml')
            assert result.exit_code == 0
            assert mock_check_creds.call_count == 1

    def test_api_key_prompt(self):
        """ Tests the API key prompt. """
        with patch('click.prompt', return_value='KEY') as mock_prompt:
            result = self.runner.invoke(runpod_cli, ['config', '--profile', 'test'])
            mock_prompt.assert_called_with('    > RunPod API Key', hide_input=False, confirmation_prompt=False) # pylint: disable=line-too-long
            assert result.exit_code == 0
