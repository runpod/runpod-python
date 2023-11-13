"""Tests for the project helpers."""

import unittest
from unittest.mock import patch, mock_open

import click

from runpod import error as rp_error
from runpod.cli.groups.project.helpers import (
    validate_project_name,
    get_project_pod,
    get_project_endpoint,
    copy_template_files,
    attempt_pod_launch,
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

    @patch("runpod.cli.groups.project.helpers.get_endpoints")
    def test_get_project_endpoint_exists(self, mock_get_endpoints):
        """Test the get_project_endpoint function when the project endpoint exists."""
        mock_get_endpoints.return_value = []
        assert get_project_endpoint("1234") is None

        mock_get_endpoints.return_value = [{"name": "test-1234", "id": "endpoint_id"}]
        result = get_project_endpoint("1234")
        self.assertEqual(result, {"name": "test-1234", "id": "endpoint_id"})

    @patch("os.listdir")
    @patch("os.path.isdir", return_value=False)
    @patch("shutil.copy2")
    def test_copy_template_files(self, mock_copy, mock_isdir, mock_listdir):
        """Test the copy_template_files function."""
        mock_listdir.return_value = ["file1.txt", "file2.txt"]
        copy_template_files("/template", "/destination")
        self.assertEqual(mock_copy.call_count, 2)
        assert mock_isdir.called

    @patch("os.listdir")
    @patch("os.path.isdir", return_value=True)
    @patch("shutil.copytree")
    def test_copy_template_files_dir(self, mock_copy, mock_isdir, mock_listdir):
        """Test the copy_template_files function."""
        mock_listdir.return_value = ["file1.txt", "file2.txt"]
        copy_template_files("/template", "/destination")
        self.assertEqual(mock_copy.call_count, 2)
        assert mock_isdir.called

    @patch("runpod.cli.groups.project.helpers.create_pod")
    def test_attempt_pod_launch_success(self, mock_create_pod):
        """Test the attempt_pod_launch function when it succeeds."""
        mock_create_pod.return_value = "pod_id"
        config = {
            "project": {
                "name": "test",
                "uuid": "1234",
                "base_image": "base_image",
                "gpu_types": ["gpu_type"],
                "gpu_count": "1",
                "ports": "ports",
                "storage_id": "storage_id",
                "volume_mount_path": "volume_mount_path",
                "container_disk_size_gb": "1"
            }
        }
        environment_variables = {"key": "value"}
        result = attempt_pod_launch(config, environment_variables)
        self.assertEqual(result, "pod_id")

        mock_create_pod.side_effect = rp_error.QueryError("error")
        assert attempt_pod_launch(config, environment_variables) is None

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data="[project]\nname='test'")
    def test_load_project_config(self, mock_file, mock_exists):
        """Test the load_project_config function."""
        config = load_project_config()
        self.assertEqual(config["project"]["name"], "test")
        assert mock_exists.called
        assert mock_file.called

        with patch("os.path.exists", return_value=False), \
                self.assertRaises(FileNotFoundError):
            load_project_config()
