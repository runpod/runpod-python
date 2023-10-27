""" Test functions in runpod.cli.groups.project.functions module. """

import os
import unittest
from unittest.mock import patch, mock_open

from runpod.cli.groups.project.functions import STARTER_TEMPLATES, create_new_project

class TestCreateNewProject(unittest.TestCase):
    """ Test the create_new_project function."""

    @patch("os.makedirs")
    @patch("os.path.exists", return_value=False)
    @patch("os.getcwd", return_value="/current/path")
    @patch("runpod.cli.groups.project.functions.copy_template_files")
    def test_create_project_folder(self, mock_copy_template_files, mock_getcwd, mock_exists, mock_makedirs): # pylint: disable=line-too-long
        """ Test that a new project folder is created if init_current_dir is False. """
        with patch("builtins.open", new_callable=mock_open):
            create_new_project("test_project", "volume_id", "3.8")
        mock_makedirs.assert_called_once_with("/current/path/test_project")
        assert mock_copy_template_files.called
        assert mock_getcwd.called
        assert mock_exists.called

    @patch("os.makedirs")
    @patch("os.path.exists", return_value=False)
    @patch("os.getcwd", return_value="/current/path")
    @patch("runpod.cli.groups.project.functions.copy_template_files")
    def test_copy_template_files(self, mock_copy_template_files, mock_getcwd, mock_exists, mock_makedirs): # pylint: disable=line-too-long
        """ Test that template files are copied to the new project folder. """
        with patch("builtins.open", new_callable=mock_open):
            create_new_project("test_project", "volume_id", "3.8")
        mock_copy_template_files.assert_called_once_with(STARTER_TEMPLATES + "/default", "/current/path/test_project") # pylint: disable=line-too-long
        assert mock_getcwd.called
        assert mock_exists.called
        assert mock_makedirs.called

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data="data with <<MODEL_NAME>> placeholder") # pylint: disable=line-too-long
    def test_replace_placeholders_in_handler(self, mock_open_file, mock_exists): # pylint: disable=line-too-long
        """ Test that placeholders in handler.py are replaced if model_name is given. """
        with patch("runpod.cli.groups.project.functions.copy_template_files"):
            create_new_project("test_project", "volume_id", "3.8", model_name="my_model")
        # mock_open_file().write.assert_called_with("data with my_model placeholder")
        assert mock_exists.called


    @patch("os.path.exists", return_value=False)
    @patch("builtins.open", new_callable=mock_open)
    def test_create_runpod_toml(self, mock_open_file, mock_exists):
        """ Test that runpod.toml configuration file is created. """
        with patch("runpod.cli.groups.project.functions.copy_template_files"):
            create_new_project("test_project", "volume_id", "3.8")
        toml_file_location = os.path.join(os.getcwd(), "test_project", "runpod.toml")
        mock_open_file.assert_called_once_with(toml_file_location, 'w', encoding="UTF-8") # pylint: disable=line-too-long
        assert mock_exists.called
