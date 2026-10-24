"""headless login arguments, prompts, and credential persistence."""

import stat
from unittest.mock import AsyncMock, MagicMock

import pytest
from click.testing import CliRunner

from runpod.apps.auth import LoginError
from runpod.cli.groups.config import functions
from runpod.rp_cli.main import cli


@pytest.fixture
def login_env(monkeypatch, tmp_path):
    credentials = tmp_path / "config.toml"
    credentials.write_text(
        '[default]\napi_key = "keep-default"\n' '[agent]\napi_key = "old-agent"\n'
    )
    monkeypatch.setattr(functions, "CREDENTIAL_FILE", str(credentials))
    monkeypatch.setattr("runpod.rp_cli.update.start_background_check", lambda: None)
    browser = AsyncMock(side_effect=AssertionError("unexpected browser login"))
    launch = MagicMock(side_effect=AssertionError("unexpected browser launch"))
    monkeypatch.setattr("runpod.apps.auth.browser_login", browser)
    monkeypatch.setattr("click.launch", launch)
    return credentials


def test_agent_saves_named_profile_and_hides_code_and_key(login_env, monkeypatch):
    async def authenticate(email, *, read_code):
        assert email == "owner@example.com"
        assert read_code() == "012345"
        return "rpa_new-agent-key"

    monkeypatch.setattr("runpod.apps.auth.agent_login", authenticate)
    result = CliRunner().invoke(
        cli,
        ["login", "--agent", "--email", " owner@example.com ", "--profile", "agent"],
        input="012345\n",
    )
    assert result.exit_code == 0, result.output
    assert "Verification code" in result.output
    assert "012345" not in result.output
    assert "rpa_new-agent-key" not in result.output
    assert functions.get_credentials("agent")["api_key"] == "rpa_new-agent-key"
    assert functions.get_credentials()["api_key"] == "keep-default"
    assert stat.S_IMODE(login_env.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "args, message",
    [
        (["--agent"], "requires --email"),
        (["--agent", "--email", " "], "requires --email"),
        (["--email", "owner@example.com"], "requires --agent"),
        (
            ["--agent", "--email", "owner@example.com", "--api-key", "rpa_input"],
            "cannot be combined",
        ),
        (
            ["--email", "owner@example.com", "--api-key", "rpa_input"],
            "requires --agent",
        ),
    ],
)
def test_invalid_options_have_no_auth_or_storage_side_effects(
    login_env, monkeypatch, args, message
):
    before = login_env.read_bytes()
    authenticate = AsyncMock()
    monkeypatch.setattr("runpod.apps.auth.agent_login", authenticate)
    result = CliRunner().invoke(cli, ["login", *args])
    assert result.exit_code == 2
    assert message in result.output
    assert "rpa_input" not in result.output
    authenticate.assert_not_called()
    assert login_env.read_bytes() == before


def test_code_format_is_validated_without_echoing_input(login_env, monkeypatch):
    async def authenticate(email, *, read_code):
        assert read_code() == "012345"
        return "rpa_test"

    monkeypatch.setattr("runpod.apps.auth.agent_login", authenticate)
    result = CliRunner().invoke(
        cli,
        ["login", "--agent", "--email", "owner@example.com", "--no-open"],
        input="abcdef\n12345\n１２３４５６\n 012345 \n",
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("Enter the six-digit code") == 3
    assert "abcdef" not in result.output
    assert "012345" not in result.output
    assert functions.get_credentials()["api_key"] == "rpa_test"


def test_eof_during_code_prompt_preserves_credentials(login_env, monkeypatch):
    before = login_env.read_bytes()

    async def authenticate(email, *, read_code):
        read_code()
        raise AssertionError("prompt must abort")

    monkeypatch.setattr("runpod.apps.auth.agent_login", authenticate)
    result = CliRunner().invoke(
        cli, ["login", "--agent", "--email", "owner@example.com"]
    )
    assert result.exit_code == 1
    assert "Aborted" in result.output
    assert login_env.read_bytes() == before


def test_auth_error_preserves_credentials(login_env, monkeypatch):
    before = login_env.read_bytes()
    monkeypatch.setattr(
        "runpod.apps.auth.agent_login",
        AsyncMock(side_effect=LoginError("code rejected")),
    )
    result = CliRunner().invoke(
        cli, ["login", "--agent", "--email", "owner@example.com"]
    )
    assert result.exit_code == 1
    assert "code rejected" in result.output
    assert "Verification code" not in result.output
    assert login_env.read_bytes() == before


def test_credential_write_failure_is_reported(login_env, monkeypatch):
    monkeypatch.setattr(
        "runpod.apps.auth.agent_login", AsyncMock(return_value="rpa_test")
    )
    monkeypatch.setattr(
        functions, "set_credentials", MagicMock(side_effect=OSError("disk full"))
    )
    result = CliRunner().invoke(
        cli, ["login", "--agent", "--email", "owner@example.com"]
    )
    assert result.exit_code == 1
    assert "disk full" in result.output
    assert "logged in" not in result.output
    assert "rpa_test" not in result.output


def test_direct_key_login_does_not_start_email_flow(login_env, monkeypatch):
    authenticate = AsyncMock()
    monkeypatch.setattr("runpod.apps.auth.agent_login", authenticate)
    result = CliRunner().invoke(cli, ["login", "--api-key", "rpa_direct"])
    assert result.exit_code == 0
    authenticate.assert_not_called()
    assert functions.get_credentials()["api_key"] == "rpa_direct"
    assert "rpa_direct" not in result.output
