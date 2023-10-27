"""Tests for the project helpers."""

import unittest
from unittest.mock import patch, mock_open

import click

from runpod.cli.groups.project.helpers import (
    validate_project_name,
    get_project_pod,
    copy_template_files,
    load_project_config
)


class TestHelpers(unittest.TestCase):
    """Test the project helpers."""

    def test_validate_project_name_valid(self):
        """Test the validate_project_name function with valid input."""
        name = "validProjectName"
        result = validate_project_name(name)
        self.assertEqual(result, name)

    def test_validate_project_name_invalid(self):
        """Test the validate_project_name function with invalid input."""
        name = "invalid:name"
        with self.assertRaises(click.BadParameter):
            validate_project_name(name)

    @patch("runpod.cli.groups.project.helpers.get_pods")
    def test_get_project_pod_exists(self, mock_get_pods):
        """Test the get_project_pod function when the project pod exists."""
        mock_get_pods.return_value = [{"name": "test-1234", "id": "pod_id"}]
        result = get_project_pod("1234")
        self.assertEqual(result, "pod_id")

    @patch("runpod.cli.groups.project.helpers.get_pods")
    def test_get_project_pod_not_exists(self, mock_get_pods):
        """Test the get_project_pod function when the project pod doesn't exist."""
        mock_get_pods.return_value = [{"name": "another-5678", "id": "another_pod_id"}]
        result = get_project_pod("1234")
        self.assertIsNone(result)

    @patch("os.listdir")
    @patch("os.path.isdir", return_value=False)
    @patch("shutil.copy2")
    def test_copy_template_files(self, mock_copy, mock_isdir, mock_listdir):
        """Test the copy_template_files function."""
        mock_listdir.return_value = ["file1.txt", "file2.txt"]
        copy_template_files("/template", "/destination")
        self.assertEqual(mock_copy.call_count, 2)
        assert mock_isdir.called

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data="[project]\nname='test'")
    def test_load_project_config(self, mock_file, mock_exists):
        """Test the load_project_config function."""
        config = load_project_config()
        self.assertEqual(config["project"]["name"], "test")
        assert mock_exists.called
        assert mock_file.called
