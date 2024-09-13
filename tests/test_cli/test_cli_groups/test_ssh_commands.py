"""Tests for the SSH commands of the CLI."""

import unittest
from unittest.mock import patch

from click.testing import CliRunner

from runpod.cli.groups.ssh.commands import add_key, list_keys


class TestSSHCommands(unittest.TestCase):
    """Tests for the SSH commands of the CLI."""

    def test_list_keys(self):
        """Test the list_keys command."""
        runner = CliRunner()
        with patch(
            "runpod.cli.groups.ssh.commands.get_user_pub_keys",
            return_value=[
                {"name": "key1", "type": "RSA", "fingerprint": "fp1"},
                {"name": "key2", "type": "DSA", "fingerprint": "fp2"},
            ],
        ) as mock_get_keys:

            result = runner.invoke(list_keys)

            self.assertIn("key1", result.output)
            self.assertIn("key2", result.output)

            assert mock_get_keys.called

    def test_add_key_without_params(self):
        """Test the add_key command without parameters."""
        runner = CliRunner()
        with patch(
            "runpod.cli.groups.ssh.commands.generate_ssh_key_pair"
        ) as mock_gen_key, patch(
            "runpod.cli.groups.ssh.commands.click.prompt", return_value="TestKey"
        ) as mock_prompt, patch(
            "runpod.cli.groups.ssh.commands.click.confirm", return_value=True
        ) as mock_confirm:  # pylint: disable=line-too-long

            result = runner.invoke(add_key, [])

            self.assertIn("The key has been added to your account.", result.output)
            mock_gen_key.assert_called_once_with("TestKey")
            assert mock_prompt.called
            assert mock_confirm.called
