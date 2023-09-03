'''
Tests for Runpod CLI exec commands.
'''
import unittest
from unittest.mock import patch

import click

from runpod.cli.exec.commands import remote_python

class TestExecCommands(unittest.TestCase):
    ''' Tests for Runpod CLI exec commands. '''

    def setUp(self):
        self.runner = click.testing.CliRunner()

    def test_remote_python_with_provided_pod_id(self):
        ''' Tests the remote_python command when pod_id is provided directly. '''
        with patch('runpod.cli.exec.commands.python_over_ssh') as mock_python_over_ssh:
            mock_python_over_ssh.return_value = None

            result = self.runner.invoke(remote_python,
                                        ['--pod_id', 'sample_pod_id', 'sample_file.py'])
            assert result.exit_code == 0
            mock_python_over_ssh.assert_called_with('sample_pod_id', 'sample_file.py')

    def test_remote_python_without_provided_pod_id_stored(self):
        ''' Tests the remote_python command when pod_id is retrieved from storage. '''
        with patch('runpod.cli.exec.commands.python_over_ssh') as mock_python_over_ssh, \
             patch('runpod.cli.utils.userspace.get_or_prompt_for_pod_id', return_value='stored_pod_id') as mock_get_pod_id: # pylint: disable=line-too-long
            mock_python_over_ssh.return_value = None
            result = self.runner.invoke(remote_python, ['sample_file.py'])
            assert result.exit_code == 0
            mock_get_pod_id.assert_called_once()
            mock_python_over_ssh.assert_called_with('stored_pod_id', 'sample_file.py')

    def test_remote_python_without_provided_pod_id_prompt(self):
        ''' Tests the remote_python command when pod_id is prompted to user. '''
        with patch('runpod.cli.exec.commands.python_over_ssh') as mock_python_over_ssh, \
             patch('runpod.cli.utils.userspace.get_or_prompt_for_pod_id', side_effect=lambda: click.prompt('Please provide the pod ID', 'prompted_pod_id')) as mock_get_pod_id: # pylint: disable=line-too-long
            mock_python_over_ssh.return_value = None
            result = self.runner.invoke(remote_python, ['sample_file.py'])
            assert result.exit_code == 0
            mock_get_pod_id.assert_called_once()
            mock_python_over_ssh.assert_called_with('prompted_pod_id', 'sample_file.py')
