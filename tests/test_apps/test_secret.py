"""secret references, env rendering, and provision-time validation."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from runpod.apps.secret import (
    Secret,
    SecretError,
    render_env,
    secret_names,
    validate_secrets,
)


class TestSecretRef:
    def test_reference_syntax(self):
        assert Secret("hf-token").reference == "{{ RUNPOD_SECRET_hf-token }}"

    def test_invalid_names_raise(self):
        with pytest.raises(SecretError):
            Secret("")
        with pytest.raises(SecretError):
            Secret("has spaces")
        with pytest.raises(SecretError):
            Secret("no}}braces")

    def test_valid_names(self):
        for name in ("hf-token", "MY_KEY", "a.b-c_d", "x1"):
            assert Secret(name).name == name


class TestRenderEnv:
    def test_mixed_env(self):
        rendered = render_env(
            {"HF_TOKEN": Secret("hf-token"), "MODE": "prod", "N": 3}
        )
        assert rendered == {
            "HF_TOKEN": "{{ RUNPOD_SECRET_hf-token }}",
            "MODE": "prod",
            "N": "3",
        }

    def test_empty(self):
        assert render_env(None) == {}
        assert render_env({}) == {}

    def test_secret_names_extraction(self):
        names = secret_names(
            {"A": Secret("one"), "B": "plain", "C": Secret("two")}
        )
        assert sorted(names) == ["one", "two"]
        assert secret_names(None) == []


class TestValidateSecrets:
    def test_existing_pass(self):
        api = AsyncMock()
        api.list_secrets.return_value = [{"name": "hf-token"}]
        asyncio.run(validate_secrets(["hf-token"], api=api))

    def test_missing_raises_with_hint(self):
        api = AsyncMock()
        api.list_secrets.return_value = [{"name": "other"}]
        with pytest.raises(SecretError, match="rp secret add"):
            asyncio.run(validate_secrets(["hf-token"], api=api))

    def test_no_references_no_api_call(self):
        api = AsyncMock()
        asyncio.run(validate_secrets([], api=api))
        api.list_secrets.assert_not_awaited()


class TestSpecManifest:
    def test_manifest_renders_secret_references(self):
        from runpod.apps.spec import ResourceKind, ResourceSpec

        spec = ResourceSpec(
            kind=ResourceKind.QUEUE,
            name="q",
            cpu=["cpu3c-1-2"],
            env={"TOKEN": Secret("tok"), "MODE": "x"},
        )
        manifest = spec.to_manifest()
        assert manifest["env"] == {
            "TOKEN": "{{ RUNPOD_SECRET_tok }}",
            "MODE": "x",
        }
