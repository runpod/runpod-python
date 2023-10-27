'''
RunPod | CLI | Utils | SSH Command
'''
import unittest
from unittest.mock import patch, MagicMock, Mock

from runpod.cli.utils.ssh_cmd import SSHConnection

class TestSSHConnection(unittest.TestCase):
    """ Test the SSHConnection class. """

    def setUp(self):

        self.patch_get_pod_ssh_ip_port = patch('runpod.cli.utils.ssh_cmd.get_pod_ssh_ip_port',
                                    return_value=('127.0.0.1', 22)).start()

        self.patch_find_ssh_key_file = patch('runpod.cli.utils.ssh_cmd.find_ssh_key_file',
                                    return_value='key_file').start()

        self.mock_ssh_client = MagicMock()
        patch_paramiko = patch('runpod.cli.utils.ssh_cmd.paramiko.SSHClient',
                               return_value=self.mock_ssh_client).start()

        self.addCleanup(self.patch_get_pod_ssh_ip_port.stop)
        self.addCleanup(self.patch_find_ssh_key_file.stop)
        self.addCleanup(patch_paramiko.stop)

        self.ssh_connection = SSHConnection('pod_id_mock')

    def test_put_file(self):
        ''' Test that put_file() calls put() on the SFTP object. '''
        local_path = '/local/file.txt'
        remote_path = '/remote/file.txt'

        mock_sftp = self.mock_ssh_client.open_sftp.return_value.__enter__.return_value
        self.ssh_connection.put_file(local_path, remote_path)

        mock_sftp.put.assert_called_once_with(local_path, remote_path)

    def test_get_file(self):
        ''' Test that get_file() calls get() on the SFTP object. '''
        local_path = '/local/file.txt'
        remote_path = '/remote/file.txt'

        mock_sftp = self.mock_ssh_client.open_sftp.return_value.__enter__.return_value
        self.ssh_connection.get_file(remote_path, local_path)

        mock_sftp.get.assert_called_once_with(remote_path, local_path)

    @patch('subprocess.run')
    def test_launch_terminal(self, mock_subprocess):
        ''' Test that launch_terminal() calls subprocess.run(). '''
        self.ssh_connection.launch_terminal()
        mock_subprocess.assert_called_once()

    @patch('subprocess.run')
    def test_rsync(self, mock_subprocess):
        ''' Test that rsync() calls subprocess.run(). '''
        self.ssh_connection.rsync('local_path', 'remote_path')
        mock_subprocess.assert_called_once()
