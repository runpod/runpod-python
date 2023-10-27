"""Tests for the SSH commands of the CLI."""

import unittest
from unittest.mock import patch
from runpod.cli.groups.ssh.commands import list_keys, add_key

class TestSSHCommands(unittest.TestCase):
    """Tests for the SSH commands of the CLI."""

    @patch('runpod.cli.groups.ssh.commands.get_user_pub_keys')
    @patch('runpod.cli.groups.ssh.commands.click.echo')
    def test_list_keys(self, mock_echo, mock_get_keys):
        """Test the list_keys command."""
        mock_get_keys.return_value = [
            {'name': 'key1', 'type': 'RSA', 'fingerprint': 'fp1'},
            {'name': 'key2', 'type': 'DSA', 'fingerprint': 'fp2'}
        ]

        list_keys()

        table_str = str(mock_echo.call_args[0][0])
        self.assertIn('key1', table_str)
        self.assertIn('key2', table_str)

    @patch('runpod.cli.groups.ssh.commands.click.echo')
    @patch('runpod.cli.groups.ssh.commands.click.confirm')
    @patch('runpod.cli.groups.ssh.commands.click.prompt')
    @patch('runpod.cli.groups.ssh.commands.generate_ssh_key_pair')
    def test_add_key_without_params(self, mock_gen_key, mock_prompt, mock_confirm, mock_echo):
        """Test the add_key command without parameters."""
        mock_confirm.return_value = True
        mock_prompt.return_value = 'TestKey'

        add_key(None, None)

        mock_gen_key.assert_called_once_with('TestKey')
        mock_echo.assert_called_once_with('The key has been added to your account.')
