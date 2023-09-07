'''
RunPod | CLI | Groups | Project | Commands | Tests
'''
import unittest
from unittest.mock import patch

from click.testing import CliRunner
from runpod.cli.groups.project.commands import new_project_wizard, launch_project_pod, start_project_pod

class TestProjectCLI(unittest.TestCase):

    def setUp(self):
        self.runner = CliRunner()

    def test_new_project_wizard_success(self):
        with patch('click.prompt', return_value='XYZ_VOLUME') as mock_prompt:
            result = self.runner.invoke(new_project_wizard, ['--name', 'TestProject', '--type', 'llama2', '--model', 'meta-llama/Llama-2-7b'])
        mock_prompt.assert_called_with("Enter the ID of the volume to use", type=str)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Project TestProject created successfully!", result.output)

    def test_new_project_wizard_invalid_name(self):
        result = self.runner.invoke(new_project_wizard, ['--name', 'Invalid/Name'])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Project name contains an invalid character", result.output)

    def test_launch_project_pod(self):
        result = self.runner.invoke(launch_project_pod)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Launching project development environment...", result.output)


    def test_start_project_pod(self):
        result = self.runner.invoke(start_project_pod, ['test_file.txt'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Starting project API server...", result.output)

    def test_start_project_pod_invalid_file(self):
        result = self.runner.invoke(start_project_pod, ['nonexistent_file.txt'])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Error: Invalid value for 'project_file': Path 'nonexistent_file.txt' does not exist.", result.output)
