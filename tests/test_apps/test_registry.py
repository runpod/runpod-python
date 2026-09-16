"""registry credential resolution."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from runpod.apps.registry import RegistryAuthError, resolve_registry_auth


def _api(creds=None):
    api = AsyncMock()
    api.list_registry_auths.return_value = creds or []
    return api


class TestResolveRegistryAuth:
    def test_none_passthrough(self):
        assert (
            asyncio.run(resolve_registry_auth(None, api=_api())) is None
        )

    def test_resolves_by_name(self):
        api = _api([{"id": "cra-1", "name": "my-ghcr"}])
        assert (
            asyncio.run(resolve_registry_auth("my-ghcr", api=api)) == "cra-1"
        )

    def test_resolves_by_id(self):
        api = _api([{"id": "cra-1", "name": "my-ghcr"}])
        assert (
            asyncio.run(resolve_registry_auth("cra-1", api=api)) == "cra-1"
        )

    def test_missing_lists_available(self):
        api = _api([{"id": "cra-1", "name": "other"}])
        with pytest.raises(
            RegistryAuthError, match="available: other"
        ):
            asyncio.run(resolve_registry_auth("ghost", api=api))

    def test_duplicates_require_id(self):
        api = _api(
            [
                {"id": "cra-1", "name": "dup"},
                {"id": "cra-2", "name": "dup"},
            ]
        )
        with pytest.raises(RegistryAuthError, match="reference by id"):
            asyncio.run(resolve_registry_auth("dup", api=api))


class TestSpecPlumbing:
    def test_decorator_accepts_registry_auth(self):
        from runpod.apps.app import App

        app = App("regtest")

        @app.queue(
            name="q",
            cpu=["cpu3c-1-2"],
            image="ghcr.io/me/x:1",
            registry_auth="my-ghcr",
        )
        def q():
            pass

        assert q.spec.registry_auth == "my-ghcr"
        assert q.spec.to_manifest()["registryAuth"] == "my-ghcr"
