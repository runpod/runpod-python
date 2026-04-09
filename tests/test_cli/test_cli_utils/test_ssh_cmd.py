"""
Runpod | CLI | Utils | SSH Command
"""

import unittest
from unittest.mock import MagicMock, patch

import paramiko

from runpod.cli.utils.ssh_cmd import SSHConnection


class TestSSHConnection(unittest.TestCase):
    """Test the SSHConnection class."""

    def setUp(self):

        self.patch_get_pod_ssh_ip_port = patch(
            "runpod.cli.utils.ssh_cmd.get_pod_ssh_ip_port",
            return_value=("127.0.0.1", 22),
        ).start()

        self.patch_find_ssh_key_file = patch(
            "runpod.cli.utils.ssh_cmd.find_ssh_key_file", return_value="key_file"
        ).start()

        self.mock_ssh_client = MagicMock()
        patch_paramiko = patch(
            "runpod.cli.utils.ssh_cmd.paramiko.SSHClient",
            return_value=self.mock_ssh_client,
        ).start()

        self.addCleanup(self.patch_get_pod_ssh_ip_port.stop)
        self.addCleanup(self.patch_find_ssh_key_file.stop)
        self.addCleanup(patch_paramiko.stop)

        self.ssh_connection = SSHConnection("pod_id_mock")

    def test_enter(self):
        """Test entering the context manager."""
        self.assertEqual(
            self.ssh_connection, self.ssh_connection.__enter__()
        )  # pylint: disable=unnecessary-dunder-call

    def test_enter_exception(self):
        """Test entering the context manager with an exception."""
        self.mock_ssh_client.connect.side_effect = paramiko.SSHException
        with self.assertRaises(SystemExit):
            SSHConnection("pod_id_mock")

    def test_exit(self):
        """Test exiting the context manager."""
        self.ssh_connection.__exit__(None, None, None)

        self.mock_ssh_client.close.assert_called_once()

    def test_run_commands(self):
        """Test that run_commands() calls exec_command() on the SSH object."""
        commands = ["command1", "command2"]

        mock_exec_command = self.mock_ssh_client.exec_command
        mock_exec_command.return_value = (
            None,
            ["stdout1", "stdout2"],
            ["stderr1", "stderr2"],
        )
        self.ssh_connection.run_commands(commands)

        assert mock_exec_command.call_count == 2

    def test_put_file(self):
        """Test that put_file() calls put() on the SFTP object."""
        local_path = "/local/file.txt"
        remote_path = "/remote/file.txt"

        mock_sftp = self.mock_ssh_client.open_sftp.return_value.__enter__.return_value
        self.ssh_connection.put_file(local_path, remote_path)

        mock_sftp.put.assert_called_once_with(local_path, remote_path)

    def test_get_file(self):
        """Test that get_file() calls get() on the SFTP object."""
        local_path = "/local/file.txt"
        remote_path = "/remote/file.txt"

        mock_sftp = self.mock_ssh_client.open_sftp.return_value.__enter__.return_value
        self.ssh_connection.get_file(remote_path, local_path)

        mock_sftp.get.assert_called_once_with(remote_path, local_path)

    @patch("subprocess.run")
    def test_launch_terminal(self, mock_subprocess):
        """Test that launch_terminal() calls subprocess.run()."""
        self.ssh_connection.launch_terminal()
        mock_subprocess.assert_called_once()

    @patch("subprocess.run")
    def test_rsync(self, mock_subprocess):
        """Test that rsync() calls subprocess.run()."""
        self.ssh_connection.rsync("local_path", "remote_path", quiet=True)
        mock_subprocess.assert_called_once()

    # Test that the signal handler closes the connection.
    @patch("runpod.cli.utils.ssh_cmd.SSHConnection.close")
    def test_signal_handler(self, mock_close):
        """Test that the signal handler closes the connection."""
        with patch("sys.exit") as mock_exit:
            self.ssh_connection._signal_handler(
                None, None
            )  # pylint: disable=protected-access
        mock_close.assert_called_once()
        assert mock_exit.called
