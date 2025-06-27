""" Test CLI pod commands """

import unittest
from unittest.mock import MagicMock, patch, mock_open

from click.testing import CliRunner
from prettytable import PrettyTable

from runpod.cli.entry import runpod_cli


class TestPodCommands(unittest.TestCase):
    """Test CLI pod commands"""

    @patch("runpod.cli.groups.pod.commands.get_pods")
    @patch("runpod.cli.groups.pod.commands.click.echo")
    def test_list_pods(self, mock_echo, mock_get_pods):
        """
        Test list_pods
        """
        # Mock data returned by get_pods
        mock_get_pods.return_value = [
            {
                "id": "1",
                "name": "Pod1",
                "desiredStatus": "Running",
                "imageName": "Image1",
            },
            {
                "id": "2",
                "name": "Pod2",
                "desiredStatus": "Stopped",
                "imageName": "Image2",
            },
        ]

        runner = CliRunner()
        result = runner.invoke(runpod_cli, ["pod", "list"])

        # Create expected table
        assert result.exit_code == 0, result.exception
        expected_table = PrettyTable(["ID", "Name", "Status", "Image"])
        expected_table.add_row(("1", "Pod1", "Running", "Image1"))
        expected_table.add_row(("2", "Pod2", "Stopped", "Image2"))

        # Assert that click.echo was called with the correct table
        mock_echo.assert_called()

    @patch("runpod.cli.groups.pod.commands.click.prompt")
    @patch("runpod.cli.groups.pod.commands.click.confirm")
    @patch("runpod.cli.groups.pod.commands.click.echo")
    @patch("runpod.cli.groups.pod.commands.create_pod")
    def test_create_new_pod(
        self, mock_create_pod, mock_echo, mock_confirm, mock_prompt
    ):  # pylint: disable=too-many-arguments,line-too-long
        """
        Test create_new_pod
        """
        # Mock values
        mock_confirm.return_value = True  # for the quick_launch option
        mock_prompt.return_value = "RunPod-CLI-Pod"
        mock_create_pod.return_value = {"id": "sample_id"}

        runner = CliRunner()
        result = runner.invoke(runpod_cli, ["pod", "create"])

        # Assertions
        assert result.exit_code == 0, result.exception
        mock_prompt.assert_called_once_with("Enter pod name", default="RunPod-CLI-Pod")
        mock_echo.assert_called_with("Pod sample_id has been created.")
        mock_create_pod.assert_called_with(
            "RunPod-CLI-Pod",
            "runpod/base:0.0.0",
            "NVIDIA GeForce RTX 3090",
            gpu_count=1,
            support_public_ip=True,
            ports="22/tcp",
        )
        mock_echo.assert_called_with("Pod sample_id has been created.")

    @patch("runpod.cli.groups.pod.commands.click.echo")
    @patch("runpod.cli.groups.pod.commands.ssh_cmd.SSHConnection")
    def test_connect_to_pod(self, mock_ssh_connection, mock_echo):
        """
        Test connect_to_pod function
        """
        pod_id = "sample_id"

        mock_ssh = MagicMock()
        mock_ssh_connection.return_value = mock_ssh

        runner = CliRunner()
        result = runner.invoke(runpod_cli, ["pod", "connect", pod_id])

        assert result.exit_code == 0, result.exception
        mock_echo.assert_called_once_with(f"Connecting to pod {pod_id}...")
        mock_ssh_connection.assert_called_once_with(pod_id)
        mock_ssh.launch_terminal.assert_called_once_with()

    @patch("runpod.cli.groups.pod.commands.os.unlink")
    @patch("runpod.cli.groups.pod.commands.tempfile.NamedTemporaryFile")
    @patch("runpod.cli.groups.pod.commands.uuid.uuid4")
    @patch("runpod.cli.groups.pod.commands.click.echo")
    @patch("runpod.cli.groups.pod.commands.ssh_cmd.SSHConnection")
    def test_sync_pods_success(self, mock_ssh_connection, mock_echo, mock_uuid, mock_temp_file, mock_unlink):
        """
        Test sync_pods function - successful sync
        """
        # Setup mocks
        mock_uuid.return_value = MagicMock()
        mock_uuid.return_value.__str__ = MagicMock(return_value="12345678-1234-1234-1234-123456789012")
        
        mock_temp_file.return_value.__enter__.return_value.name = "/tmp/test_archive.tar.gz"
        
        # Mock SSH connections
        mock_source_ssh = MagicMock()
        mock_dest_ssh = MagicMock()
        
        # Mock SSH exec_command responses
        mock_source_ssh.ssh.exec_command.side_effect = [
            (None, MagicMock(read=lambda: b"42"), None),  # file count
            (None, MagicMock(read=lambda: b"exists"), None),  # directory exists check
            (None, MagicMock(read=lambda: b"created"), None),  # archive created check
            (None, MagicMock(read=lambda: b"1.5M"), None),  # archive size
        ]
        
        mock_dest_ssh.ssh.exec_command.return_value = (None, MagicMock(read=lambda: b"42"), None)  # dest file count
        
        # Configure SSH connection context manager
        def ssh_side_effect(pod_id):
            if pod_id == "source_pod":
                mock_source_ssh.__enter__ = MagicMock(return_value=mock_source_ssh)
                mock_source_ssh.__exit__ = MagicMock(return_value=None)
                return mock_source_ssh
            elif pod_id == "dest_pod":
                mock_dest_ssh.__enter__ = MagicMock(return_value=mock_dest_ssh)
                mock_dest_ssh.__exit__ = MagicMock(return_value=None)
                return mock_dest_ssh
            
        mock_ssh_connection.side_effect = ssh_side_effect
        
        # Mock SSH key validation
        with patch("runpod.cli.groups.ssh.functions.get_user_pub_keys") as mock_get_keys:
            mock_get_keys.return_value = [{"name": "test-key", "type": "ssh-rsa", "fingerprint": "SHA256:test"}]
            
            runner = CliRunner()
            result = runner.invoke(runpod_cli, ["pod", "sync", "source_pod", "dest_pod", "/workspace", "/workspace"])

        assert result.exit_code == 0, result.exception
        
        # Verify SSH connections were created
        assert mock_ssh_connection.call_count == 2
        mock_ssh_connection.assert_any_call("source_pod")
        mock_ssh_connection.assert_any_call("dest_pod")
        
        # Verify file operations
        mock_source_ssh.get_file.assert_called_once()
        mock_dest_ssh.put_file.assert_called_once()
        
        # Verify commands were run
        mock_source_ssh.run_commands.assert_called()
        mock_dest_ssh.run_commands.assert_called()

    @patch("runpod.cli.groups.pod.commands.uuid.uuid4")
    @patch("runpod.cli.groups.pod.commands.click.echo")
    @patch("runpod.cli.groups.pod.commands.ssh_cmd.SSHConnection")
    def test_sync_pods_no_ssh_keys(self, mock_ssh_connection, mock_echo, mock_uuid):
        """
        Test sync_pods function - no SSH keys configured
        """
        # Setup mocks
        mock_uuid.return_value = MagicMock()
        mock_uuid.return_value.__str__ = MagicMock(return_value="12345678-1234-1234-1234-123456789012")
        
        # Mock SSH key validation - no keys found
        with patch("runpod.cli.groups.ssh.functions.get_user_pub_keys") as mock_get_keys:
            mock_get_keys.return_value = []  # No SSH keys
            
            runner = CliRunner()
            result = runner.invoke(runpod_cli, ["pod", "sync", "source_pod", "dest_pod"])

        assert result.exit_code == 0, result.exception
        
        # Verify error message was shown
        mock_echo.assert_any_call("❌ No SSH keys found in your Runpod account!")
        mock_echo.assert_any_call("🔑 To create an SSH key, run:")
        mock_echo.assert_any_call("   runpod ssh add-key")

    @patch("runpod.cli.groups.pod.commands.uuid.uuid4")
    @patch("runpod.cli.groups.pod.commands.click.echo")
    @patch("runpod.cli.groups.pod.commands.ssh_cmd.SSHConnection")
    def test_sync_pods_source_not_found(self, mock_ssh_connection, mock_echo, mock_uuid):
        """
        Test sync_pods function - source directory not found
        """
        # Setup mocks
        mock_uuid.return_value = MagicMock()
        mock_uuid.return_value.__str__ = MagicMock(return_value="12345678-1234-1234-1234-123456789012")
        
        mock_source_ssh = MagicMock()
        mock_source_ssh.__enter__ = MagicMock(return_value=mock_source_ssh)
        mock_source_ssh.__exit__ = MagicMock(return_value=None)
        
        # Mock SSH exec_command responses - directory doesn't exist
        mock_source_ssh.ssh.exec_command.side_effect = [
            (None, MagicMock(read=lambda: b"0"), None),  # file count
            (None, MagicMock(read=lambda: b"not_found"), None),  # directory exists check
        ]
        
        mock_ssh_connection.return_value = mock_source_ssh
        
        # Mock SSH key validation
        with patch("runpod.cli.groups.ssh.functions.get_user_pub_keys") as mock_get_keys:
            mock_get_keys.return_value = [{"name": "test-key", "type": "ssh-rsa", "fingerprint": "SHA256:test"}]
            
            runner = CliRunner()
            result = runner.invoke(runpod_cli, ["pod", "sync", "source_pod", "dest_pod", "/nonexistent", "/workspace"])

        assert result.exit_code == 0, result.exception
        
        # Verify error message was shown
        mock_echo.assert_any_call("❌ Error: Source workspace /nonexistent does not exist on pod source_pod")

    @patch("runpod.cli.groups.pod.commands.uuid.uuid4")
    @patch("runpod.cli.groups.pod.commands.click.echo")
    @patch("runpod.cli.groups.pod.commands.ssh_cmd.SSHConnection")
    def test_sync_pods_archive_creation_failed(self, mock_ssh_connection, mock_echo, mock_uuid):
        """
        Test sync_pods function - archive creation failed
        """
        # Setup mocks
        mock_uuid.return_value = MagicMock()
        mock_uuid.return_value.__str__ = MagicMock(return_value="12345678-1234-1234-1234-123456789012")
        
        mock_source_ssh = MagicMock()
        mock_source_ssh.__enter__ = MagicMock(return_value=mock_source_ssh)
        mock_source_ssh.__exit__ = MagicMock(return_value=None)
        
        # Mock SSH exec_command responses - archive creation fails
        mock_source_ssh.ssh.exec_command.side_effect = [
            (None, MagicMock(read=lambda: b"42"), None),  # file count
            (None, MagicMock(read=lambda: b"exists"), None),  # directory exists check
            (None, MagicMock(read=lambda: b"failed"), None),  # archive created check fails
        ]
        
        mock_ssh_connection.return_value = mock_source_ssh
        
        # Mock SSH key validation
        with patch("runpod.cli.groups.ssh.functions.get_user_pub_keys") as mock_get_keys:
            mock_get_keys.return_value = [{"name": "test-key", "type": "ssh-rsa", "fingerprint": "SHA256:test"}]
            
            runner = CliRunner()
            result = runner.invoke(runpod_cli, ["pod", "sync", "source_pod", "dest_pod", "/workspace", "/workspace"])

        assert result.exit_code == 0, result.exception
        
        # Verify error message was shown
        mock_echo.assert_any_call("❌ Error: Failed to create archive on source pod")

    @patch("runpod.cli.groups.pod.commands.uuid.uuid4")
    @patch("runpod.cli.groups.pod.commands.click.echo")
    @patch("runpod.cli.groups.pod.commands.ssh_cmd.SSHConnection")
    def test_sync_pods_ssh_exception(self, mock_ssh_connection, mock_echo, mock_uuid):
        """
        Test sync_pods function - SSH connection exception
        """
        # Setup mocks
        mock_uuid.return_value = MagicMock()
        mock_uuid.return_value.__str__ = MagicMock(return_value="12345678-1234-1234-1234-123456789012")
        
        # Mock SSH connection to raise exception
        mock_ssh_connection.side_effect = Exception("SSH connection failed")
        
        # Mock SSH key validation
        with patch("runpod.cli.groups.ssh.functions.get_user_pub_keys") as mock_get_keys:
            mock_get_keys.return_value = [{"name": "test-key", "type": "ssh-rsa", "fingerprint": "SHA256:test"}]
            
            runner = CliRunner()
            result = runner.invoke(runpod_cli, ["pod", "sync", "source_pod", "dest_pod", "/workspace", "/workspace"])

        assert result.exit_code == 0, result.exception
        
        # Verify error message was shown
        mock_echo.assert_any_call("❌ Error during sync: SSH connection failed")

    @patch("runpod.cli.groups.pod.commands.uuid.uuid4")
    @patch("runpod.cli.groups.pod.commands.click.echo")
    @patch("runpod.cli.groups.pod.commands.ssh_cmd.SSHConnection")
    def test_sync_pods_default_workspace(self, mock_ssh_connection, mock_echo, mock_uuid):
        """
        Test sync_pods function - using default workspace paths
        """
        # Setup mocks
        mock_uuid.return_value = MagicMock()
        mock_uuid.return_value.__str__ = MagicMock(return_value="12345678-1234-1234-1234-123456789012")
        
        mock_source_ssh = MagicMock()
        mock_source_ssh.__enter__ = MagicMock(return_value=mock_source_ssh)
        mock_source_ssh.__exit__ = MagicMock(return_value=None)
        
        # Mock SSH exec_command responses
        mock_source_ssh.ssh.exec_command.side_effect = [
            (None, MagicMock(read=lambda: b"10"), None),  # file count
            (None, MagicMock(read=lambda: b"exists"), None),  # directory exists check
        ]
        
        mock_ssh_connection.return_value = mock_source_ssh
        
        # Mock SSH key validation
        with patch("runpod.cli.groups.ssh.functions.get_user_pub_keys") as mock_get_keys:
            mock_get_keys.return_value = [{"name": "test-key", "type": "ssh-rsa", "fingerprint": "SHA256:test"}]
            
            runner = CliRunner()
            # Test with only pod IDs (should use /workspace as default)
            result = runner.invoke(runpod_cli, ["pod", "sync", "source_pod", "dest_pod"])

        assert result.exit_code == 0, result.exception
        
        # Verify the default workspace path is used
        mock_echo.assert_any_call("🔄 Syncing from source_pod:/workspace to dest_pod:/workspace")
