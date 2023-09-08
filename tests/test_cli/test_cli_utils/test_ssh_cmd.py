'''
RunPod | CLI | Utils | SSH Command
'''
import tempfile
import unittest
from unittest.mock import patch, MagicMock, mock_open

from runpod.cli.utils.ssh_cmd import SSHConnection

class TestSSHConnection(unittest.TestCase):

    def setUp(self):
        patcher1 = patch('runpod.cli.utils.ssh_cmd.get_ssh_ip_port',
                         return_value=('127.0.0.1', 22))
        self.mock_get_ssh_ip_port = patcher1.start()
        self.addCleanup(patcher1.stop)

        # Create a temporary file to use as a mock key file
        self.mock_key_file = tempfile.NamedTemporaryFile()

        patcher2 = patch('runpod.cli.utils.ssh_cmd.find_ssh_key_file',
                         return_value=self.mock_key_file.name)
        self.mock_find_ssh_key_file = patcher2.start()
        self.addCleanup(patcher2.stop)

        self.ssh_connection = SSHConnection('pod_id_mock')

    @patch.object(SSHConnection, 'run_commands')
    def test_run_commands(self, mock_run_commands):
        '''
        Test that run_commands() calls run_commands() on the SSHConnection object.
        '''
        commands = ['ls', 'pwd']
        self.ssh_connection.run_commands(commands)
        mock_run_commands.assert_called_once()

    @patch('os.listdir')
    @patch('os.path.isdir', return_value=False)
    @patch('paramiko.SSHClient.open_sftp')
    def test_put_directory_files(self, mock_sftp, mock_isdir, mock_listdir):
        '''
        Test that put_directory() calls put() on the SFTP object for each file in the directory.
        '''
        mock_sftp_obj = mock_sftp.return_value.__enter__.return_value
        mock_sftp_obj.stat.side_effect = [IOError, None]
        mock_listdir.return_value = ['file1.txt', 'file2.txt']

        local_path = '/local/path'
        remote_path = '/remote/path'

        self.ssh_connection.put_directory(local_path, remote_path)

        mock_sftp_obj.put.assert_called()
        mock_sftp_obj.mkdir.assert_called_once_with(remote_path)

    @patch('paramiko.SSHClient.open_sftp')
    def test_put_file(self, mock_sftp):
        """
        Test that put_file() calls put() on the SFTP object.
        """
        mock_sftp_obj = mock_sftp.return_value.__enter__.return_value
        local_path = '/local/file.txt'
        remote_path = '/remote/file.txt'

        self.ssh_connection.put_file(local_path, remote_path)

        mock_sftp_obj.put.assert_called_once_with(local_path, remote_path)

    @patch('paramiko.SSHClient.open_sftp')
    def test_get_file(self, mock_sftp):
        """
        Test that get_file() calls get() on the SFTP object.
        """
        mock_sftp_obj = mock_sftp.return_value.__enter__.return_value
        local_path = '/local/file.txt'
        remote_path = '/remote/file.txt'

        self.ssh_connection.get_file(remote_path, local_path)

        mock_sftp_obj.get.assert_called_once_with(remote_path, local_path)

    def test_close(self):
        """
        Test that close() calls close() on the SSHClient object.
        """
        self.ssh_connection.close()
        self.ssh_connection.ssh.close.assert_called_once()

    @patch('subprocess.run')
    def test_launch_terminal(self, mock_subprocess):
        """
        Test that launch_terminal() calls subprocess.run().
        """
        self.ssh_connection.launch_terminal()
        mock_subprocess.assert_called_once()
