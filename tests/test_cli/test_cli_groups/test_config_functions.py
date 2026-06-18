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

    @patch("runpod.cli.groups.config.functions.os.replace")
    @patch("runpod.cli.groups.config.functions.os.unlink")
    @patch("runpod.cli.groups.config.functions.os.fdopen", new_callable=mock_open)
    @patch("runpod.cli.groups.config.functions.tempfile.mkstemp")
    @patch("runpod.cli.groups.config.functions.tomlkit.dump")
    @patch("runpod.cli.groups.config.functions.tomlkit.document")
    @patch("runpod.cli.groups.config.functions.Path.touch")
    @patch("runpod.cli.groups.config.functions.os.makedirs")
    @patch("builtins.open", new_callable=mock_open, read_data="")
    def test_set_credentials(
        self, mock_file, _mock_makedirs, _mock_touch, mock_document,
        mock_dump, mock_mkstemp, _mock_fdopen, _mock_unlink, _mock_replace,
    ):
        """
        Tests the set_credentials function.
        """
        mock_mkstemp.return_value = (99, "/tmp/cred.toml")
        mock_document.side_effect = [{}, {"default": True}]
        functions.set_credentials("RUNPOD_API_KEY")

        assert any(
            call.args[0] == functions.CREDENTIAL_FILE
            and call.args[1] == "r"
            and call.kwargs.get("encoding") == "UTF-8"
            for call in mock_file.call_args_list
        )
        assert mock_dump.called

        with self.assertRaises(ValueError) as context:
            functions.set_credentials("RUNPOD_API_KEY")

        self.assertEqual(
            str(context.exception),
            "Profile already exists. Use set_credentials(overwrite=True) to update.",
        )

    @patch("runpod.cli.groups.config.functions.os.replace")
    @patch("runpod.cli.groups.config.functions.os.unlink")
    @patch("runpod.cli.groups.config.functions.os.fdopen", new_callable=mock_open)
    @patch("runpod.cli.groups.config.functions.tempfile.mkstemp")
    @patch("runpod.cli.groups.config.functions.tomlkit.dump")
    @patch("runpod.cli.groups.config.functions.Path.touch")
    @patch("runpod.cli.groups.config.functions.os.makedirs")
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data='[default]\napi_key = "EXISTING_KEY"\n\n[profile1]\napi_key = "KEY1"\n',
    )
    def test_set_credentials_preserves_existing_profiles(
        self, _mock_file, _mock_makedirs, _mock_touch, mock_dump,
        mock_mkstemp, _mock_fdopen, _mock_unlink, _mock_replace,
    ):
        """Adding a new profile must preserve all existing profiles."""
        mock_mkstemp.return_value = (99, "/tmp/cred.toml")
        functions.set_credentials("NEW_KEY", profile="profile2")

        dumped_config = mock_dump.call_args[0][0]
        assert "default" in dumped_config
        assert dumped_config["default"]["api_key"] == "EXISTING_KEY"
        assert "profile1" in dumped_config
        assert dumped_config["profile1"]["api_key"] == "KEY1"
        assert "profile2" in dumped_config
        assert dumped_config["profile2"]["api_key"] == "NEW_KEY"

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

    @patch("runpod.cli.groups.config.functions.os.path.exists", return_value=True)
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

    @patch("runpod.cli.groups.config.functions.os.path.exists", return_value=True)
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

    @patch("runpod.cli.groups.config.functions.os.replace")
    @patch("runpod.cli.groups.config.functions.os.unlink")
    @patch("runpod.cli.groups.config.functions.os.fdopen", new_callable=mock_open)
    @patch("runpod.cli.groups.config.functions.tempfile.mkstemp")
    @patch("runpod.cli.groups.config.functions.tomlkit.dump")
    @patch("runpod.cli.groups.config.functions.Path.touch")
    @patch("runpod.cli.groups.config.functions.os.makedirs")
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data='[default]\napi_key = "OLD_KEY"\n',
    )
    def test_set_credentials_overwrite_replaces_existing_profile(
        self, _mock_file, _mock_makedirs, _mock_touch, mock_dump,
        mock_mkstemp, _mock_fdopen, _mock_unlink, _mock_replace,
    ):
        """overwrite=True replaces an existing profile's api_key."""
        mock_mkstemp.return_value = (99, "/tmp/cred.toml")
        functions.set_credentials("NEW_KEY", profile="default", overwrite=True)

        dumped_config = mock_dump.call_args[0][0]
        assert dumped_config["default"]["api_key"] == "NEW_KEY"

    @patch("runpod.cli.groups.config.functions.os.replace")
    @patch("runpod.cli.groups.config.functions.os.unlink")
    @patch("runpod.cli.groups.config.functions.os.fdopen", new_callable=mock_open)
    @patch("runpod.cli.groups.config.functions.tempfile.mkstemp")
    @patch("runpod.cli.groups.config.functions.tomlkit.dump", side_effect=OSError("disk full"))
    @patch("runpod.cli.groups.config.functions.Path.touch")
    @patch("runpod.cli.groups.config.functions.os.makedirs")
    @patch("builtins.open", new_callable=mock_open, read_data="")
    def test_set_credentials_cleans_up_temp_on_dump_failure(
        self, _mock_file, _mock_makedirs, _mock_touch, _mock_dump,
        mock_mkstemp, _mock_fdopen, mock_unlink, mock_replace,
    ):
        """Temp file is removed and original config untouched when dump fails."""
        mock_mkstemp.return_value = (99, "/tmp/cred.toml")
        with self.assertRaises(OSError):
            functions.set_credentials("KEY")

        mock_unlink.assert_called_once_with("/tmp/cred.toml")
        mock_replace.assert_not_called()

    @patch("runpod.cli.groups.config.functions.os.replace")
    @patch("runpod.cli.groups.config.functions.os.unlink")
    @patch("runpod.cli.groups.config.functions.tempfile.mkstemp")
    @patch("runpod.cli.groups.config.functions.tomlkit.dump")
    @patch("runpod.cli.groups.config.functions.Path.touch")
    @patch("runpod.cli.groups.config.functions.os.makedirs")
    @patch("builtins.open", new_callable=mock_open, read_data="not valid toml {{{")
    def test_set_credentials_corrupted_toml_raises(
        self, _mock_file, _mock_makedirs, _mock_touch, _mock_dump,
        _mock_mkstemp, _mock_unlink, _mock_replace,
    ):
        """set_credentials raises ValueError on corrupted TOML regardless of overwrite."""
        with self.assertRaises(ValueError):
            functions.set_credentials("NEW_KEY", overwrite=True)
        with self.assertRaises(ValueError):
            functions.set_credentials("NEW_KEY", overwrite=False)
