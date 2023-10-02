''' Tests for CLI group `exec functions` '''

import unittest
from unittest.mock import patch, MagicMock

from runpod.cli.groups.exec import functions

class TestExecFunctions(unittest.TestCase):
    ''' Tests for CLI group `exec functions` '''

    @patch('runpod.cli.groups.exec.functions.ssh_cmd.SSHConnection')
    def test_python_over_ssh(self, mock_ssh_connection):
        '''
        Test `python_over_ssh`
        '''
        mock_ssh = MagicMock()
        mock_ssh_connection.return_value = mock_ssh

        pod_id = 'pod_id'
        file_name = 'file_name'

        functions.python_over_ssh(pod_id, file_name)

        self.assertTrue(functions.python_over_ssh)
        mock_ssh_connection.assert_called_once_with(pod_id)
        mock_ssh.put_file.assert_called_once_with(file_name, f'/root/{file_name}')
        mock_ssh.run_commands.assert_called_once_with([f'python3.10 /root/{file_name}'])
        mock_ssh.close.assert_called_once_with()
