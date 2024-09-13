"""
Unit tests for the config command.
"""

import unittest
from unittest.mock import mock_open, patch

from runpod.cli.groups.config import functions


class TestConfig(unittest.TestCase):
    """Unit tests for the config function."""

    def setUp(self) -> None:
        self.sample_credentials = "[default]\n" 'api_key = "RUNPOD_API_KEY"\n'

    @patch("runpod.cli.groups.config.functions.toml.load")
    @patch("builtins.open", new_callable=mock_open())
    def test_set_credentials(self, mock_file, mock_toml_load):
        """
        Tests the set_credentials function.
        """
        mock_toml_load.return_value = ""
        functions.set_credentials("RUNPOD_API_KEY")

        mock_file.assert_called_with(functions.CREDENTIAL_FILE, "w", encoding="UTF-8")

        with self.assertRaises(ValueError) as context:
            mock_toml_load.return_value = {"default": True}
            functions.set_credentials("RUNPOD_API_KEY")

        self.assertEqual(
            str(context.exception),
            "Profile already exists. Use `update_credentials` instead.",
        )

    @patch("builtins.open", new_callable=mock_open())
    @patch("runpod.cli.groups.config.functions.toml.load")
    @patch("runpod.cli.groups.config.functions.os.path.exists")
    def test_check_credentials(self, mock_exists, mock_toml_load, mock_file):
        """mock_open_call
        Tests the check_credentials function.
        """
        mock_exists.return_value = False
        passed, _ = functions.check_credentials()
        assert passed is False

        mock_exists.return_value = True
        mock_toml_load.return_value = ""
        passed, _ = functions.check_credentials()
        assert mock_file.called
        assert passed is False

        mock_exists.return_value = True
        mock_toml_load.return_value = dict({"default": "something"})
        passed, _ = functions.check_credentials()
        assert passed is False

        mock_toml_load.return_value = ValueError
        passed, _ = functions.check_credentials()
        assert passed is False

        mock_toml_load.return_value = dict({"default": "api_key"})
        passed, _ = functions.check_credentials()
        assert passed is True

    @patch("os.path.exists", return_value=True)
    @patch("runpod.cli.groups.config.functions.toml.load")
    @patch(
        "builtins.open", new_callable=mock_open, read_data='[default]\nkey = "value"'
    )
    def test_get_credentials_existing_profile(
        self, mock_open_call, mock_toml_load, mock_exists
    ):
        """
        Tests the get_credentials function.
        """
        mock_toml_load.return_value = {"default": {"key": "value"}}
        result = functions.get_credentials("default")
        assert result == {"key": "value"}
        assert mock_open_call.called
        assert mock_exists.called

    @patch("os.path.exists", return_value=True)
    @patch("runpod.cli.groups.config.functions.toml.load")
    @patch(
        "builtins.open", new_callable=mock_open, read_data='[default]\nkey = "value"'
    )
    def test_get_credentials_non_existent_profile(
        self, mock_open_call, mock_toml_load, mock_exists
    ):  # pylint: disable=line-too-long
        """
        Tests the get_credentials function.
        """
        mock_toml_load.return_value = {"default": {"key": "value"}}
        result = functions.get_credentials("non_existent")
        assert result is None
        assert mock_open_call.called
        assert mock_exists.called
