"""rp ssh: pod-id dispatch and subcommand resolution."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from runpod.rp_cli.main import cli


class TestSSHDispatch:
    def test_pod_id_routes_to_connect(self):
        runner = CliRunner()
        with patch(
            "runpod.cli.utils.ssh_cmd.SSHConnection"
        ) as connection:
            connection.return_value = MagicMock()
            result = runner.invoke(cli, ["ssh", "pod-abc123"])
        assert "Connecting to pod pod-abc123" in result.output
        connection.assert_called_once_with("pod-abc123")

    def test_connect_subcommand(self):
        runner = CliRunner()
        with patch(
            "runpod.cli.utils.ssh_cmd.SSHConnection"
        ) as connection:
            connection.return_value = MagicMock()
            result = runner.invoke(cli, ["ssh", "connect", "pod-abc123"])
        assert "Connecting to pod pod-abc123" in result.output

    def test_named_subcommands_still_resolve(self):
        runner = CliRunner()
        with patch(
            "runpod.cli.groups.ssh.commands.get_user_pub_keys", return_value=[]
        ):
            result = runner.invoke(cli, ["ssh", "list"])
        assert result.exit_code == 0

    def test_bare_ssh_shows_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["ssh"])
        assert "Commands:" in result.output
