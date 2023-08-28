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

    def test_runpod_cli(self):
        ''' Tests the runpod_cli command. '''
        with patch('click.echo') as mock_echo:
            result = self.runner.invoke(runpod_cli)
            self.assertEqual(result.exit_code, 0)
            mock_echo.assert_called_once()

    def test_store_api_key(self):
        ''' Tests the store_api_key command. '''
        with patch('click.echo') as mock_echo:
            with patch('runpod.cli.commands.set_credentials') as mock_set_credentials:
                result = self.runner.invoke(
                    runpod_cli, ['store_api_key', 'API_KEY_1234', '--profile', 'test'])
                self.assertEqual(result.exit_code, 0)
                mock_set_credentials.assert_called_once()
                mock_echo.assert_called_once()

if __name__ == "__main__":
    unittest.main()
