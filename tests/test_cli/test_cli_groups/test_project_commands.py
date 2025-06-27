"""
Runpod | CLI | Groups | Project | Commands | Tests
"""

import unittest
from unittest.mock import patch

from click.testing import CliRunner

from runpod.cli.groups.project.commands import (
    deploy_project,
    new_project_wizard,
    start_project_pod,
)


class TestProjectCLI(unittest.TestCase):
    """A collection of tests for the Project CLI commands."""

    def setUp(self):
        self.runner = CliRunner()

    def test_new_project_wizard_no_network_volumes(self):
        """
        Tests the new_project_wizard command with no network volumes.
        """
        with patch("runpod.cli.groups.project.commands.get_user") as mock_get_user:
            mock_get_user.return_value = {"networkVolumes": []}

            result = self.runner.invoke(new_project_wizard)

        self.assertEqual(result.exit_code, 1)
        self.assertIn("You do not have any network volumes.", result.output)

    def test_new_project_wizard_success(self):
        """
        Tests the new_project_wizard command.
        """
        with patch("click.prompt") as mock_prompt, patch(
            "click.confirm", return_value=True
        ) as mock_confirm, patch(
            "runpod.cli.groups.project.commands.create_new_project"
        ) as mock_create, patch(
            "runpod.cli.groups.project.commands.get_user"
        ) as mock_get_user, patch(
            "runpod.cli.groups.project.commands.cli_select"
        ) as mock_select:
            mock_get_user.return_value = {
                "networkVolumes": [
                    {
                        "id": "XYZ_VOLUME",
                        "name": "XYZ_VOLUME",
                        "size": 100,
                        "dataCenterId": "XYZ",
                    }
                ]
            }  # pylint: disable=line-too-long
            mock_prompt.side_effect = ["TestProject", "11.8.0", "3.10"]
            mock_select.return_value = {"volume-id": "XYZ_VOLUME"}

            result = self.runner.invoke(
                new_project_wizard,
                ["--type", "llama2", "--model", "meta-llama/Llama-2-7b"],
            )  # pylint: disable=line-too-long

        self.assertEqual(result.exit_code, 0)
        mock_confirm.assert_called_with("Do you want to continue?", abort=True)
        mock_create.assert_called()
        mock_prompt.assert_called()
        mock_create.assert_called_with(
            "TestProject",
            "XYZ_VOLUME",
            "11.8.0",
            "3.10",
            "llama2",
            "meta-llama/Llama-2-7b",
            False,
        )  # pylint: disable=line-too-long
        self.assertIn("Project TestProject created successfully!", result.output)

    def test_new_project_wizard_success_init_current_dir(self):
        """
        Tests the new_project_wizard command with the --init flag.
        """
        with patch("click.prompt") as mock_prompt, patch(
            "click.confirm", return_value=True
        ) as mock_confirm, patch(
            "runpod.cli.groups.project.commands.create_new_project"
        ) as mock_create, patch(
            "runpod.cli.groups.project.commands.get_user"
        ) as mock_get_user, patch(
            "runpod.cli.groups.project.commands.cli_select"
        ) as mock_select, patch(
            "os.getcwd"
        ) as mock_getcwd:
            mock_get_user.return_value = {
                "networkVolumes": [
                    {
                        "id": "XYZ_VOLUME",
                        "name": "XYZ_VOLUME",
                        "size": 100,
                        "dataCenterId": "XYZ",
                    }
                ]
            }  # pylint: disable=line-too-long
            mock_select.return_value = {"volume-id": "XYZ_VOLUME"}
            mock_prompt.side_effect = ["XYZ_VOLUME", "11.8.0", "3.10"]

            self.runner.invoke(new_project_wizard, ["--init"])
            assert mock_confirm.called
            assert mock_create.called
            assert mock_getcwd.called

    def test_new_project_wizard_invalid_name(self):
        """
        Tests the new_project_wizard command with an invalid project name.
        """
        with patch("runpod.cli.groups.project.commands.get_user") as mock_get_user:
            mock_get_user.return_value = {"networkVolumes": ["XYZ_VOLUME"]}

            result = self.runner.invoke(new_project_wizard, ["--name", "Invalid/Name"])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("Project name contains an invalid character", result.output)

    def test_start_project_pod(self):
        """
        Tests the start_project_pod command.
        """
        with patch("click.confirm", return_value=True) as mock_confirm, patch(
            "runpod.cli.groups.project.commands.start_project"
        ) as mock_start:
            mock_start.return_value = None
            result = self.runner.invoke(start_project_pod)

        mock_confirm.assert_called_with("Do you want to continue?", abort=True)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Starting project development pod...", result.output)

    @patch("runpod.cli.groups.project.commands.click.echo")
    @patch("runpod.cli.groups.project.commands.create_project_endpoint")
    def test_deploy_project(self, mock_create_project_endpoint, mock_click_echo):
        """Test the deploy_project function."""
        mock_create_project_endpoint.return_value = "test_endpoint_id"

        result = self.runner.invoke(deploy_project)

        mock_create_project_endpoint.assert_called_once()

        mock_click_echo.assert_any_call("Deploying project...")
        mock_click_echo.assert_any_call("The following urls are available:")
        mock_click_echo.assert_any_call(
            "    - https://api.runpod.ai/v2/test_endpoint_id/runsync"
        )
        mock_click_echo.assert_any_call(
            "    - https://api.runpod.ai/v2/test_endpoint_id/run"
        )
        mock_click_echo.assert_any_call(
            "    - https://api.runpod.ai/v2/test_endpoint_id/health"
        )

        self.assertEqual(result.exit_code, 0)
