"""shared authentication and package boundaries."""

import os
import importlib
import subprocess
import sys

import pytest

import runpod
from runpod.auth import credentials, load_api_key, resolve_api_key
from runpod.error import AuthenticationError


def test_environment_key_precedes_stored_profile(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "environment-key")

    def unexpected_read(*args):
        raise AssertionError("credentials should not be read")

    monkeypatch.setattr(credentials, "get_credentials", unexpected_read)
    assert load_api_key("agent") == "environment-key"


def test_load_selected_profile(monkeypatch, tmp_path):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.setattr(credentials, "CREDENTIAL_FILE", str(tmp_path / "config.toml"))
    credentials.set_credentials("default-key")
    credentials.set_credentials("agent-key", profile="agent")
    assert load_api_key() == "default-key"
    assert load_api_key("agent") == "agent-key"
    assert load_api_key("missing") is None


def test_missing_credentials_are_optional(monkeypatch, tmp_path):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.setattr(credentials, "CREDENTIAL_FILE", str(tmp_path / "missing.toml"))
    assert load_api_key() is None


def test_resolve_explicit_and_configured_keys(monkeypatch):
    monkeypatch.setattr(runpod, "api_key", "configured-key")
    assert resolve_api_key("explicit-key") == "explicit-key"
    assert resolve_api_key() == "configured-key"
    monkeypatch.setattr(runpod, "api_key", None)
    with pytest.raises(AuthenticationError, match="No API key provided"):
        resolve_api_key()


def test_transports_share_key_resolution():
    from runpod.api import graphql, rest

    assert graphql._resolve_api_key is resolve_api_key
    assert rest._resolve_api_key is resolve_api_key


def test_public_credential_functions_use_auth_package():
    assert runpod.get_credentials is credentials.get_credentials
    assert runpod.set_credentials is credentials.set_credentials
    assert runpod.check_credentials is credentials.check_credentials


def test_flash_credential_import_uses_shared_module(monkeypatch, tmp_path):
    import runpod.cli.groups.config.functions as flash_credentials

    assert flash_credentials is credentials
    assert importlib.import_module("runpod.cli.groups.config.functions") is credentials
    monkeypatch.setattr(
        flash_credentials, "CREDENTIAL_FILE", str(tmp_path / "config.toml")
    )
    flash_credentials.set_credentials("profile-key")
    assert credentials.get_credentials() == {"api_key": "profile-key"}


def test_login_import_does_not_load_apps_or_cli():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, runpod.auth.login; "
            "assert 'runpod.apps' not in sys.modules; "
            "assert 'runpod.cli' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("environment_key", [None, "environment-key"])
def test_sdk_import_does_not_load_cli_or_login(monkeypatch, tmp_path, environment_key):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    if environment_key is not None:
        monkeypatch.setenv("RUNPOD_API_KEY", environment_key)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, runpod; "
            "assert 'runpod.cli' not in sys.modules; "
            "assert 'runpod.auth.login' not in sys.modules; "
            "assert 'runpod.apps' not in sys.modules; "
            "assert 'click' not in sys.modules; "
            "assert 'paramiko' not in sys.modules",
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
