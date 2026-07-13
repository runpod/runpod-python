"""tests for app/env/secret/registry/logs cli commands."""

from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from runpod.apps.manage import UndeployResult
from runpod.rp_cli.main import cli


def _runner():
    return CliRunner()


class TestAppCommands:
    def test_list_empty(self):
        with patch("runpod.apps.manage.list_apps", AsyncMock(return_value=[])):
            result = _runner().invoke(cli, ["app", "list"])
        assert result.exit_code == 0
        assert "no apps deployed" in result.output

    def test_list(self):
        apps = [
            {
                "name": "demo",
                "flashEnvironments": [{"name": "default"}, {"name": "prod"}],
            },
            {"name": "other", "flashEnvironments": []},
        ]
        with patch(
            "runpod.apps.manage.list_apps", AsyncMock(return_value=apps)
        ):
            result = _runner().invoke(cli, ["app", "list"])
        assert result.exit_code == 0
        assert "demo" in result.output
        assert "default, prod" in result.output

    def test_get(self):
        entry = {
            "name": "demo",
            "flashEnvironments": [
                {"name": "default", "activeBuildId": "build123456789"}
            ],
        }
        with patch(
            "runpod.apps.manage.get_app", AsyncMock(return_value=entry)
        ):
            result = _runner().invoke(cli, ["app", "get", "demo"])
        assert result.exit_code == 0
        assert "build1234567" in result.output

    def test_get_error(self):
        with patch(
            "runpod.apps.manage.get_app",
            AsyncMock(side_effect=RuntimeError("no app named 'demo'")),
        ):
            result = _runner().invoke(cli, ["app", "get", "demo"])
        assert result.exit_code == 1
        assert "no app named" in result.output

    def test_delete_confirmed(self):
        outcome = UndeployResult(endpoints_deleted=2, failures=[])
        with patch(
            "runpod.apps.manage.delete_app", AsyncMock(return_value=outcome)
        ):
            result = _runner().invoke(cli, ["app", "delete", "demo", "--yes"])
        assert result.exit_code == 0

    def test_delete_aborts_without_confirmation(self):
        result = _runner().invoke(cli, ["app", "delete", "demo"], input="n\n")
        assert result.exit_code == 1

    def test_delete_failures_keep_app(self):
        outcome = UndeployResult(endpoints_deleted=0, failures=["ep1 stuck"])
        with patch(
            "runpod.apps.manage.delete_app", AsyncMock(return_value=outcome)
        ):
            result = _runner().invoke(cli, ["app", "delete", "demo", "--yes"])
        assert result.exit_code == 1
        assert "undeploy incomplete" in result.output


class TestEnvCommands:
    def test_list(self):
        entry = {
            "flashEnvironments": [
                {
                    "name": "default",
                    "activeBuildId": "build123456789",
                    "createdAt": "2026-01-02T03:04:05Z",
                }
            ]
        }
        with patch(
            "runpod.apps.manage.get_app", AsyncMock(return_value=entry)
        ):
            result = _runner().invoke(
                cli, ["env", "list", "--app", "demo"]
            )
        assert result.exit_code == 0
        assert "default" in result.output

    def test_list_empty(self):
        with patch(
            "runpod.apps.manage.get_app",
            AsyncMock(return_value={"flashEnvironments": []}),
        ):
            result = _runner().invoke(cli, ["env", "list", "--app", "demo"])
        assert result.exit_code == 0
        assert "no environments" in result.output

    def test_get(self):
        entry = {
            "name": "prod",
            "activeBuildId": "build123456789",
            "endpoints": [{"name": "chat", "id": "ep1"}],
        }
        with patch(
            "runpod.apps.manage.get_environment",
            AsyncMock(return_value=entry),
        ):
            result = _runner().invoke(
                cli, ["env", "get", "prod", "--app", "demo"]
            )
        assert result.exit_code == 0
        assert "demo/prod" in result.output
        assert "chat" in result.output

    def test_add(self):
        client = MagicMock()
        client.create_environment = AsyncMock(return_value={"id": "env1"})
        with (
            patch("runpod.apps.api.AppsApiClient", return_value=client),
            patch(
                "runpod.apps.manage.get_app",
                AsyncMock(return_value={"id": "app1"}),
            ),
        ):
            result = _runner().invoke(
                cli, ["env", "add", "staging", "--app", "demo"]
            )
        assert result.exit_code == 0
        client.create_environment.assert_awaited_once_with("app1", "staging")

    def test_delete(self):
        outcome = UndeployResult(endpoints_deleted=1, failures=[])
        with patch(
            "runpod.apps.manage.undeploy_environment",
            AsyncMock(return_value=outcome),
        ):
            result = _runner().invoke(
                cli, ["env", "delete", "prod", "--app", "demo", "--yes"]
            )
        assert result.exit_code == 0

    def test_resolve_app_name_fails_without_unique_app(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = _runner().invoke(cli, ["env", "list"])
        assert result.exit_code == 1
        assert "--app" in result.output


class TestUndeploy:
    def test_undeploy(self):
        outcome = UndeployResult(endpoints_deleted=3, failures=[])
        with patch(
            "runpod.apps.manage.undeploy_environment",
            AsyncMock(return_value=outcome),
        ):
            result = _runner().invoke(
                cli, ["undeploy", "--app", "demo", "--yes"]
            )
        assert result.exit_code == 0
        assert "3 endpoints removed" in result.output

    def test_undeploy_failures(self):
        outcome = UndeployResult(endpoints_deleted=0, failures=["boom"])
        with patch(
            "runpod.apps.manage.undeploy_environment",
            AsyncMock(return_value=outcome),
        ):
            result = _runner().invoke(
                cli, ["undeploy", "--app", "demo", "--yes"]
            )
        assert result.exit_code == 1


class TestSecretCommands:
    def _client(self):
        client = MagicMock()
        client.create_secret = AsyncMock(return_value={"id": "s1"})
        client.list_secrets = AsyncMock(
            return_value=[
                {"id": "s1", "name": "tok", "description": "a token"}
            ]
        )
        client.delete_secret = AsyncMock(return_value=True)
        return client

    def test_add(self):
        client = self._client()
        with patch("runpod.apps.api.AppsApiClient", return_value=client):
            result = _runner().invoke(
                cli, ["secret", "add", "tok", "--value", "v"]
            )
        assert result.exit_code == 0
        client.create_secret.assert_awaited_once_with("tok", "v", "")

    def test_add_prompts(self):
        client = self._client()
        with patch("runpod.apps.api.AppsApiClient", return_value=client):
            result = _runner().invoke(
                cli, ["secret", "add"], input="tok\nv\n"
            )
        assert result.exit_code == 0

    def test_list(self):
        client = self._client()
        with patch("runpod.apps.api.AppsApiClient", return_value=client):
            result = _runner().invoke(cli, ["secret", "list"])
        assert result.exit_code == 0
        assert "tok" in result.output

    def test_list_empty(self):
        client = self._client()
        client.list_secrets = AsyncMock(return_value=[])
        with patch("runpod.apps.api.AppsApiClient", return_value=client):
            result = _runner().invoke(cli, ["secret", "list"])
        assert "no secrets" in result.output

    def test_delete(self):
        client = self._client()
        with patch("runpod.apps.api.AppsApiClient", return_value=client):
            result = _runner().invoke(
                cli, ["secret", "delete", "tok", "--yes"]
            )
        assert result.exit_code == 0
        client.delete_secret.assert_awaited_once_with("s1")

    def test_delete_unknown(self):
        client = self._client()
        client.list_secrets = AsyncMock(return_value=[])
        with patch("runpod.apps.api.AppsApiClient", return_value=client):
            result = _runner().invoke(
                cli, ["secret", "delete", "tok", "--yes"]
            )
        assert result.exit_code == 1
        assert "no secret named" in result.output


class TestRegistryCommands:
    def _client(self):
        client = MagicMock()
        client.create_registry_auth = AsyncMock(return_value={"id": "r1"})
        client.list_registry_auths = AsyncMock(
            return_value=[{"id": "r1", "name": "dockerhub"}]
        )
        client.delete_registry_auth = AsyncMock(return_value=True)
        return client

    def test_add(self):
        client = self._client()
        with patch("runpod.apps.api.AppsApiClient", return_value=client):
            result = _runner().invoke(
                cli,
                [
                    "registry", "add", "dockerhub",
                    "--username", "u", "--password", "p",
                ],
            )
        assert result.exit_code == 0
        client.create_registry_auth.assert_awaited_once_with(
            "dockerhub", "u", "p"
        )

    def test_list(self):
        client = self._client()
        with patch("runpod.apps.api.AppsApiClient", return_value=client):
            result = _runner().invoke(cli, ["registry", "list"])
        assert result.exit_code == 0
        assert "dockerhub" in result.output

    def test_delete(self):
        client = self._client()
        with patch("runpod.apps.api.AppsApiClient", return_value=client):
            result = _runner().invoke(
                cli, ["registry", "delete", "dockerhub", "--yes"]
            )
        assert result.exit_code == 0
        client.delete_registry_auth.assert_awaited_once_with("r1")

    def test_delete_unknown(self):
        client = self._client()
        client.list_registry_auths = AsyncMock(return_value=[])
        with patch("runpod.apps.api.AppsApiClient", return_value=client):
            result = _runner().invoke(
                cli, ["registry", "delete", "nope", "--yes"]
            )
        assert result.exit_code == 1


class TestLogsCommand:
    def test_snapshot(self):
        logs = {"system": ["booted"], "container": ["hello"]}
        with patch(
            "runpod.apps.logs.pod_logs", AsyncMock(return_value=logs)
        ):
            result = _runner().invoke(cli, ["logs", "pod1"])
        assert result.exit_code == 0
        assert "[system] booted" in result.output
        assert "[container] hello" in result.output

    def test_follow(self):
        async def fake_stream(pod_id, **kwargs):
            yield {"source": "container", "line": "streamed"}

        with patch("runpod.apps.logs.stream_pod_logs", fake_stream):
            result = _runner().invoke(cli, ["logs", "pod1", "--follow"])
        assert result.exit_code == 0
        assert "[container] streamed" in result.output


class TestLoginCommand:
    def test_api_key_flag(self):
        with patch(
            "runpod.cli.groups.config.functions.set_credentials"
        ) as creds:
            result = _runner().invoke(
                cli, ["login", "--api-key", "sk-test"]
            )
        assert result.exit_code == 0
        creds.assert_called_once_with("sk-test", overwrite=True)

    def test_browser_flow(self):
        with (
            patch(
                "runpod.apps.auth.browser_login",
                AsyncMock(return_value="sk-granted"),
            ),
            patch(
                "runpod.cli.groups.config.functions.set_credentials"
            ) as creds,
        ):
            result = _runner().invoke(cli, ["login", "--no-open"])
        assert result.exit_code == 0
        creds.assert_called_once_with("sk-granted", overwrite=True)

    def test_browser_flow_error(self):
        from runpod.apps.auth import LoginError

        with patch(
            "runpod.apps.auth.browser_login",
            AsyncMock(side_effect=LoginError("expired")),
        ):
            result = _runner().invoke(cli, ["login", "--no-open"])
        assert result.exit_code == 1
        assert "expired" in result.output
