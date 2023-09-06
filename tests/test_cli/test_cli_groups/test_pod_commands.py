''' Test CLI pod commands '''

import unittest
from unittest.mock import patch

from click.testing import CliRunner
from prettytable import PrettyTable

from runpod.cli.entry import runpod_cli

class TestPodCommands(unittest.TestCase):
    ''' Test CLI pod commands '''

    @patch('runpod.cli.groups.pod.commands.get_pods')
    @patch('runpod.cli.groups.pod.commands.click.echo')
    def test_list_pods(self, mock_echo, mock_get_pods):
        '''
        Test list_pods
        '''
        # Mock data returned by get_pods
        mock_get_pods.return_value = [
            {'id': '1', 'name': 'Pod1', 'desiredStatus': 'Running', 'imageName': 'Image1'},
            {'id': '2', 'name': 'Pod2', 'desiredStatus': 'Stopped', 'imageName': 'Image2'}
        ]

        runner = CliRunner()
        result = runner.invoke(runpod_cli, ['pod', 'list'])

        # Create expected table
        assert result.exit_code == 0, result.exception
        expected_table = PrettyTable(['ID', 'Name', 'Status', 'Image'])
        expected_table.add_row(('1', 'Pod1', 'Running', 'Image1'))
        expected_table.add_row(('2', 'Pod2', 'Stopped', 'Image2'))

        # Assert that click.echo was called with the correct table
        mock_echo.assert_called_once_with(expected_table)


    @patch('runpod.cli.groups.pod.commands.click.prompt')
    @patch('runpod.cli.groups.pod.commands.click.confirm')
    @patch('runpod.cli.groups.pod.commands.click.echo')
    @patch('runpod.cli.groups.pod.commands.create_pod')
    @patch('runpod.cli.groups.pod.commands.pod_from_template')
    def test_create_new_pod(self, mock_pod_from_template, mock_create_pod, mock_echo, mock_confirm, mock_prompt): # pylint: disable=too-many-arguments,line-too-long
        '''
        Test create_new_pod
        '''
        mock_pod_from_template.return_value = None

        # Mock values
        mock_confirm.return_value = True  # for the quick_launch option
        mock_prompt.return_value = 'RunPod-CLI-Pod'
        mock_create_pod.return_value = {'id': 'sample_id'}

        runner = CliRunner()
        result = runner.invoke(runpod_cli, [
                'pod', 'create',
                'test_name',
                '--image', 'runpod/base:0.0.0',
                '--gpu-type', 'NVIDIA GeForce RTX 3090',
                '--gpu-count', '1',
                '--support-public-ip'
            ]
        )

        # Assertions
        assert result.exit_code == 0, result.exception
        mock_prompt.assert_called_once_with('Enter pod name', default='RunPod-CLI-Pod')
        mock_echo.assert_called_with('Launching default pod...')
        mock_create_pod.assert_called_with('RunPod-CLI-Pod',
                                           'runpod/base:0.0.0',
                                           'NVIDIA GeForce RTX 3090',
                                           gpu_count=1, support_public_ip=True, ports='22/tcp')
        mock_echo.assert_called_with('Pod sample_id has been created.')
