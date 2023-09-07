import unittest
from click.testing import CliRunner
from runpod.cli.groups.project.commands import new_project_wizard, launch_project_pod, start_project_pod

class TestProjectCLI(unittest.TestCase):

    def setUp(self):
        self.runner = CliRunner()

    # Testing new_project_wizard

    def test_new_project_wizard_success(self):
        result = self.runner.invoke(new_project_wizard, ['--name', 'TestProject', '--type', 'llama2', '--model', 'meta-llama/Llama-2-7b'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Project TestProject created successfully!", result.output)

    def test_new_project_wizard_invalid_name(self):
        result = self.runner.invoke(new_project_wizard, ['--name', 'Invalid/Name'])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Project name contains an invalid character", result.output)

    # Testing launch_project_pod

    def test_launch_project_pod(self):
        result = self.runner.invoke(launch_project_pod)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Launching project development environment...", result.output)

    # Testing start_project_pod

    def test_start_project_pod(self):
        # Assuming a file named 'test_file.txt' exists in the current directory.
        result = self.runner.invoke(start_project_pod, ['test_file.txt'])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Starting project API server...", result.output)

    def test_start_project_pod_invalid_file(self):
        result = self.runner.invoke(start_project_pod, ['nonexistent_file.txt'])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Error: Invalid value for 'project_file': Path 'nonexistent_file.txt' does not exist.", result.output)
