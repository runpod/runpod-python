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

    @patch("os.path.exists", return_value=True)
    @patch(
        "runpod.cli.groups.config.functions.toml.load",
        side_effect=ValueError("Invalid value"),
    )
    @patch("builtins.open", new_callable=mock_open)
    def test_get_credentials_corrupted_toml(
        self, _mock_open_call, _mock_toml_load, _mock_exists
    ):
        """get_credentials returns None when config.toml contains invalid TOML."""
        result = functions.get_credentials("default")
        assert result is None

    @patch("os.path.exists", return_value=True)
    @patch(
        "runpod.cli.groups.config.functions.toml.load",
        side_effect=TypeError("bad type"),
    )
    @patch("builtins.open", new_callable=mock_open)
    def test_get_credentials_type_error(
        self, _mock_open_call, _mock_toml_load, _mock_exists
    ):
        """get_credentials returns None on TypeError from corrupted file."""
        result = functions.get_credentials("default")
        assert result is None

    @patch("runpod.cli.groups.config.functions.toml.load")
    @patch("builtins.open", new_callable=mock_open())
    def test_set_credentials_corrupted_toml_allows_overwrite(
        self, _mock_file, mock_toml_load
    ):
        """set_credentials with overwrite=True ignores corrupted existing file."""
        mock_toml_load.side_effect = ValueError("Invalid TOML")
        # overwrite=True skips the toml.load check entirely
        functions.set_credentials("NEW_KEY", overwrite=True)

    @patch("runpod.cli.groups.config.functions.toml.load")
    @patch("builtins.open", new_callable=mock_open())
    def test_set_credentials_corrupted_toml_no_overwrite(
        self, _mock_file, mock_toml_load
    ):
        """set_credentials without overwrite treats corrupted file as empty."""
        mock_toml_load.side_effect = ValueError("Invalid TOML")
        # Should not raise — corrupted file is treated as having no profiles
        functions.set_credentials("NEW_KEY", overwrite=False)
