"""Credential profile selection via RUNPOD_PROFILE."""

import pytest

import runpod


class TestSelectedProfile:
    def test_defaults_to_default_profile(self, monkeypatch):
        monkeypatch.delenv("RUNPOD_PROFILE", raising=False)
        assert runpod._selected_profile() == "default"

    def test_env_selects_profile(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_PROFILE", "team")
        assert runpod._selected_profile() == "team"

    def test_env_value_is_stripped(self, monkeypatch):
        monkeypatch.setenv("RUNPOD_PROFILE", "  team ")
        assert runpod._selected_profile() == "team"

    @pytest.mark.parametrize("value", ["", "   "])
    def test_blank_env_falls_back_to_default(self, monkeypatch, value):
        monkeypatch.setenv("RUNPOD_PROFILE", value)
        assert runpod._selected_profile() == "default"


class TestApiKeyFromSelectedProfile:
    def test_loads_key_from_selected_profile(self, tmp_path, monkeypatch):
        from runpod.cli.groups.config import functions

        credentials = tmp_path / "config.toml"
        credentials.write_text(
            '[default]\napi_key = "default-key"\n[team]\napi_key = "team-key"\n'
        )
        monkeypatch.setattr(functions, "CREDENTIAL_FILE", str(credentials))
        monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
        monkeypatch.setattr(runpod, "profile", "team")

        assert runpod._load_api_key() == "team-key"

    def test_api_key_env_wins_over_profile(self, tmp_path, monkeypatch):
        from runpod.cli.groups.config import functions

        credentials = tmp_path / "config.toml"
        credentials.write_text('[team]\napi_key = "team-key"\n')
        monkeypatch.setattr(functions, "CREDENTIAL_FILE", str(credentials))
        monkeypatch.setenv("RUNPOD_API_KEY", "env-key")
        monkeypatch.setattr(runpod, "profile", "team")

        assert runpod._load_api_key() == "env-key"
