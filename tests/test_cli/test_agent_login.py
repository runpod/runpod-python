"""headless login arguments, instructions, and credential persistence."""

import shlex
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
    monkeypatch.setattr(
        "click.prompt", MagicMock(side_effect=AssertionError("unexpected prompt"))
    )
    monkeypatch.setattr(
        "runpod.apps.auth.start_agent_login",
        AsyncMock(
            return_value={
                "attempt_id": "att_test",
                "verifier": "private",
                "api_url": "https://api.runpod.io/v2/signup/authentication-attempts",
            }
        ),
    )
    return credentials


def test_agent_saves_named_profile_and_hides_code_and_key(login_env, monkeypatch):
    from runpod.apps.auth import load_agent_login

    async def authenticate(email):
        assert email == "owner@example.com"
        return {
            "attempt_id": "att_test",
            "verifier": "private",
            "api_url": "https://api.runpod.io/v2/signup/authentication-attempts",
        }

    monkeypatch.setattr("runpod.apps.auth.start_agent_login", authenticate)
    verify = AsyncMock(return_value="rpa_new-agent-key")
    monkeypatch.setattr("runpod.apps.auth.verify_agent_login", verify)
    result = CliRunner().invoke(
        cli,
        ["login", "--agent", "--email", " owner@example.com ", "--profile", "agent"],
    )
    assert result.exit_code == 0, result.output
    assert "Check your inbox" in result.output
    assert "runpod login --agent --code CODE --profile agent" in result.output
    assert functions.get_credentials("agent")["api_key"] == "old-agent"
    attempt = load_agent_login("agent")
    result = CliRunner().invoke(
        cli, ["login", "--agent", "--code", "012345", "--profile", "agent"]
    )
    assert result.exit_code == 0, result.output
    verify.assert_awaited_once_with(attempt, "012345")
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
    monkeypatch.setattr("runpod.apps.auth.start_agent_login", authenticate)
    result = CliRunner().invoke(cli, ["login", *args])
    assert result.exit_code == 2
    assert message in result.output
    assert "rpa_input" not in result.output
    authenticate.assert_not_called()
    assert login_env.read_bytes() == before


def test_code_format_is_validated_without_echoing_input(login_env, monkeypatch):
    verify = AsyncMock(return_value="rpa_test")
    monkeypatch.setattr("runpod.apps.auth.verify_agent_login", verify)
    runner = CliRunner()
    sent = runner.invoke(
        cli, ["login", "--agent", "--email", "owner@example.com", "--no-open"]
    )
    assert sent.exit_code == 0, sent.output
    for code in ("abcdef", "12345", "１２３４５６"):
        result = runner.invoke(cli, ["login", "--agent", "--code", code])
        assert result.exit_code == 2
        assert "six ASCII digits" in result.output
        assert code not in result.output
    verify.assert_not_called()
    result = runner.invoke(cli, ["login", "--agent", "--code", " 012345 "])
    assert result.exit_code == 0, result.output
    assert verify.await_args.args[1] == "012345"
    assert "012345" not in result.output
    assert functions.get_credentials()["api_key"] == "rpa_test"


def test_email_request_exits_without_stdin_and_preserves_credentials(login_env):
    before = login_env.read_bytes()
    result = CliRunner().invoke(
        cli, ["login", "--agent", "--email", "owner@example.com"]
    )
    assert result.exit_code == 0, result.output
    assert "runpod login --agent --code CODE" in result.output
    assert login_env.read_bytes() == before


def test_auth_error_preserves_credentials(login_env, monkeypatch):
    before = login_env.read_bytes()
    monkeypatch.setattr(
        "runpod.apps.auth.start_agent_login",
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
    sent = CliRunner().invoke(cli, ["login", "--agent", "--email", "owner@example.com"])
    assert sent.exit_code == 0, sent.output
    monkeypatch.setattr(
        "runpod.apps.auth.verify_agent_login", AsyncMock(return_value="rpa_test")
    )
    monkeypatch.setattr(
        functions, "set_credentials", MagicMock(side_effect=OSError("disk full"))
    )
    result = CliRunner().invoke(cli, ["login", "--agent", "--code", "012345"])
    assert result.exit_code == 1
    assert "disk full" in result.output
    assert "logged in" not in result.output
    assert "rpa_test" not in result.output


def test_direct_key_login_does_not_start_email_flow(login_env, monkeypatch):
    authenticate = AsyncMock()
    monkeypatch.setattr("runpod.apps.auth.start_agent_login", authenticate)
    result = CliRunner().invoke(cli, ["login", "--api-key", "rpa_direct"])
    assert result.exit_code == 0
    authenticate.assert_not_called()
    assert functions.get_credentials()["api_key"] == "rpa_direct"
    assert "rpa_direct" not in result.output


def test_agent_retry_explains_rejection_without_resending_notice(
    login_env, monkeypatch
):
    verify = AsyncMock(
        side_effect=[LoginError("verification code rejected"), "rpa_test"]
    )
    monkeypatch.setattr("runpod.apps.auth.verify_agent_login", verify)
    runner = CliRunner()
    sent = runner.invoke(cli, ["login", "--agent", "--email", "owner@example.com"])
    assert sent.exit_code == 0, sent.output
    assert sent.output.count("A verification code was sent") == 1
    rejected = runner.invoke(cli, ["login", "--agent", "--code", "123456"])
    assert rejected.exit_code == 1
    assert "verification code rejected" in rejected.output
    assert "123456" not in rejected.output
    assert "A verification code was sent" not in rejected.output
    result = runner.invoke(cli, ["login", "--agent", "--code", "012345"])
    assert result.exit_code == 0, result.output
    assert "A verification code was sent" not in result.output
    assert verify.await_count == 2
    assert verify.await_args_list[0].args[0] == verify.await_args_list[1].args[0]
    assert "123456" not in result.output
    assert "012345" not in result.output
    assert "rpa_test" not in result.output
    assert functions.get_credentials()["api_key"] == "rpa_test"


def test_agent_help_explains_two_step_default(login_env):
    result = CliRunner().invoke(cli, ["login", "--help"])
    assert result.exit_code == 0
    help_text = " ".join(result.output.split())
    assert "--agent --email ADDRESS" in help_text
    assert "--agent --code CODE" in help_text
    assert "complete login without prompting" in help_text
    assert "--send-code" not in help_text


@pytest.mark.parametrize(
    "args, message",
    [
        (["--code", "012345"], "requires --agent"),
        (
            ["--agent", "--email", "owner@example.com", "--code", "012345"],
            "pending login",
        ),
        (
            ["--agent", "--code", "012345", "--api-key", "secret-key"],
            "cannot be combined",
        ),
        *[
            (["--agent", "--code", code], "six ASCII digits")
            for code in ("", "12345", "1234567", "abcdef", "１２３４５６")
        ],
    ],
)
def test_two_step_invalid_options_do_not_authenticate(
    login_env, monkeypatch, args, message
):
    start = AsyncMock()
    verify = AsyncMock()
    monkeypatch.setattr("runpod.apps.auth.start_agent_login", start)
    monkeypatch.setattr("runpod.apps.auth.verify_agent_login", verify)
    before = login_env.read_bytes()
    result = CliRunner().invoke(cli, ["login", *args])
    assert result.exit_code == 2
    assert message in result.output
    start.assert_not_called()
    verify.assert_not_called()
    assert login_env.read_bytes() == before
    assert "secret-key" not in result.output


def test_two_step_missing_state_does_not_authenticate(login_env, monkeypatch):
    verify = AsyncMock()
    monkeypatch.setattr("runpod.apps.auth.verify_agent_login", verify)
    result = CliRunner().invoke(cli, ["login", "--agent", "--code", "012345"])
    assert result.exit_code == 1
    assert "no pending login" in result.output
    verify.assert_not_called()


@pytest.mark.parametrize(
    "content", ["not json", "[]", "{}", '{"verifier": "sensitive"}']
)
def test_two_step_invalid_state_is_sanitized(login_env, monkeypatch, content):
    from runpod.apps.auth import pending_agent_login_path

    pending_agent_login_path("default").write_text(content)
    verify = AsyncMock()
    monkeypatch.setattr("runpod.apps.auth.verify_agent_login", verify)
    result = CliRunner().invoke(cli, ["login", "--agent", "--code", "012345"])
    assert result.exit_code == 1
    assert "invalid pending login" in result.output
    assert "sensitive" not in result.output
    verify.assert_not_called()


def test_pending_logins_are_private_and_profile_scoped(login_env):
    from runpod.apps.auth import (
        load_agent_login,
        pending_agent_login_path,
        save_agent_login,
    )

    for profile in ("default", "agent", "../../outside"):
        attempt = {
            "attempt_id": profile,
            "verifier": "private",
            "api_url": "https://api.runpod.io",
        }
        save_agent_login(attempt, profile)
        path = pending_agent_login_path(profile)
        assert path.parent == login_env.parent
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert load_agent_login(profile) == attempt
    assert len(list(login_env.parent.glob("login-*.json"))) == 3


def test_two_step_api_change_does_not_send_verifier(login_env, monkeypatch):
    from runpod.apps.auth import save_agent_login

    save_agent_login(
        {
            "attempt_id": "att_test",
            "verifier": "private",
            "api_url": "https://other.example/v2/signup/authentication-attempts",
        },
        "default",
    )
    monkeypatch.setenv("RUNPOD_API_BASE_URL", "https://api.runpod.io")
    post = AsyncMock()
    monkeypatch.setattr("runpod.apps.auth._post_authentication_attempt", post)
    result = CliRunner().invoke(cli, ["login", "--agent", "--code", "012345"])
    assert result.exit_code == 1
    assert "different API" in result.output
    assert "private" not in result.output
    post.assert_not_called()


def test_two_step_help_documents_noninteractive_commands(login_env):
    result = CliRunner().invoke(cli, ["login", "--help"])
    assert result.exit_code == 0
    help_text = " ".join(result.output.split())
    assert "--agent --email ADDRESS" in help_text
    assert "--agent --code CODE" in help_text
    assert "same --profile" in help_text
    assert "shell history and process listings" in help_text


@pytest.mark.parametrize(
    "profile", ["default", "agent", "agent's profile; echo unsafe"]
)
def test_email_request_prints_shell_safe_completion_command(login_env, profile):
    result = CliRunner().invoke(
        cli, ["login", "--agent", "--email", "owner@example.com", "--profile", profile]
    )
    assert result.exit_code == 0, result.output
    assert "Check your inbox, then run:" in result.output
    assert "Replace CODE with the six-digit code from your email." in result.output
    (command,) = [
        line.strip()
        for line in result.output.splitlines()
        if line.strip().startswith("runpod login")
    ]
    expected = ["runpod", "login", "--agent", "--code", "CODE"]
    if profile != "default":
        expected.extend(["--profile", profile])
    assert shlex.split(command) == expected
    assert "private" not in result.output


def test_pending_login_atomic_write_preserves_previous_attempt(login_env, monkeypatch):
    from runpod.apps.auth import (
        load_agent_login,
        pending_agent_login_path,
        save_agent_login,
    )

    attempt = {
        "attempt_id": "att_old",
        "verifier": "private",
        "api_url": "https://api.runpod.io",
    }
    save_agent_login(attempt, "default")
    monkeypatch.setattr(
        "runpod.apps.auth.os.replace", MagicMock(side_effect=OSError("disk full"))
    )
    with pytest.raises(OSError, match="disk full"):
        save_agent_login({**attempt, "attempt_id": "att_new"}, "default")
    assert load_agent_login("default") == attempt
    assert list(login_env.parent.glob("*.json")) == [
        pending_agent_login_path("default")
    ]


def test_two_step_credential_write_failure_keeps_pending_state(login_env, monkeypatch):
    from runpod.apps.auth import load_agent_login, save_agent_login

    attempt = {
        "attempt_id": "att_test",
        "verifier": "private",
        "api_url": "https://api.runpod.io",
    }
    save_agent_login(attempt, "default")
    monkeypatch.setattr(
        "runpod.apps.auth.verify_agent_login", AsyncMock(return_value="rpa_test")
    )
    monkeypatch.setattr(
        functions, "set_credentials", MagicMock(side_effect=OSError("disk full"))
    )
    before = login_env.read_bytes()
    result = CliRunner().invoke(cli, ["login", "--agent", "--code", "012345"])
    assert result.exit_code == 1
    assert "disk full" in result.output
    assert "rpa_test" not in result.output
    assert load_agent_login("default") == attempt
    assert login_env.read_bytes() == before
