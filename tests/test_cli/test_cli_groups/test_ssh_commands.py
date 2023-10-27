""" Tests for the SSH commands in the CLI. """

import unittest
from unittest.mock import patch, Mock

import click

from runpod.cli.groups.project.functions import start_project_api


class TestStartProjectApi(unittest.TestCase):
    """ Tests for the start_project_api function. """

    @patch('runpod.cli.project.functions.load_project_config')
    @patch('runpod.cli.project.functions.get_project_pod')
    @patch('runpod.cli.utils.ssh_cmd.SSHConnection')
    def test_start_project_api_pod_not_found(self, mock_ssh_connection, mock_get_project_pod, mock_load_project_config): # pylint: disable=line-too-long
        """ Test that an exception is raised if the project pod isn't found. """
        config = {'project': {'uuid': 'test-uuid'}}
        mock_load_project_config.return_value = config
        mock_get_project_pod.return_value = None

        # Expect a ClickException to be raised if the pod isn't found
        with self.assertRaises(click.ClickException) as context:
            start_project_api()

        self.assertEqual(
            str(context.exception),
            'Project pod not found for uuid: test-uuid. Try running "runpod project launch" first.'
        )

        assert mock_ssh_connection.call_count == 0

    @patch('runpod.cli.groups.project.functions.load_project_config')
    @patch('runpod.cli.groups.project.functions.get_project_pod')
    @patch('runpod.cli.utils.ssh_cmd.SSHConnection')
    def test_start_project_api_pod_found(self, mock_ssh_connection, mock_get_project_pod, mock_load_project_config): # pylint: disable=line-too-long
        """ Test that the SSHConnection is called with the project pod if it is found. """
        config = {'project': {'uuid': 'test-uuid'}}
        mock_load_project_config.return_value = config
        mock_project_pod = Mock()
        mock_get_project_pod.return_value = mock_project_pod

        # Execute function (assuming it doesn't throw any exceptions for this test case)
        start_project_api()

        # Ensure the SSHConnection was called with the mock project pod
        mock_ssh_connection.assert_called_once_with(mock_project_pod)

        assert mock_ssh_connection.call_count == 1
