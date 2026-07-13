"""app/environment lifecycle: list, inspect, undeploy, delete."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from runpod.apps.manage import (
    AppNotFound,
    EnvironmentNotFound,
    delete_app,
    get_app,
    get_environment,
    list_apps,
    undeploy_environment,
)


def _api(**overrides):
    api = AsyncMock()
    api.list_apps.return_value = []
    api.get_app_by_name.return_value = None
    api.get_environment_by_name.return_value = None
    api.delete_endpoint.return_value = True
    api.delete_environment.return_value = True
    api.delete_app.return_value = True
    for key, value in overrides.items():
        getattr(api, key).return_value = value
    return api


class TestQueries:
    def test_list_sorted_by_name(self):
        api = _api(
            list_apps=[{"name": "zeta"}, {"name": "alpha"}]
        )
        apps = asyncio.run(list_apps(api=api))
        assert [a["name"] for a in apps] == ["alpha", "zeta"]

    def test_get_app_not_found(self):
        with pytest.raises(AppNotFound, match="rp deploy"):
            asyncio.run(get_app("ghost", api=_api()))

    def test_get_environment_not_found(self):
        with pytest.raises(EnvironmentNotFound, match="ghost"):
            asyncio.run(get_environment("app", "ghost", api=_api()))


class TestUndeployEnvironment:
    def _env(self, n_endpoints=2):
        return {
            "id": "env-1",
            "name": "default",
            "endpoints": [
                {"id": f"ep-{i}", "name": f"svc-{i}"}
                for i in range(n_endpoints)
            ],
        }

    def test_deletes_endpoints_then_environment(self):
        api = _api(get_environment_by_name=self._env())
        result = asyncio.run(
            undeploy_environment("app", "default", api=api)
        )
        assert result.endpoints_deleted == 2
        assert result.environment_deleted
        assert not result.failures
        api.delete_environment.assert_awaited_once_with("env-1")

    def test_keep_env_flag(self):
        api = _api(get_environment_by_name=self._env())
        result = asyncio.run(
            undeploy_environment("app", "default", api=api, delete_env=False)
        )
        assert result.endpoints_deleted == 2
        assert not result.environment_deleted
        api.delete_environment.assert_not_awaited()

    def test_endpoint_failure_keeps_environment(self):
        api = _api(get_environment_by_name=self._env(1))
        api.delete_endpoint.side_effect = RuntimeError("in use")
        result = asyncio.run(
            undeploy_environment("app", "default", api=api)
        )
        assert result.endpoints_deleted == 0
        assert not result.environment_deleted
        assert result.failures
        api.delete_environment.assert_not_awaited()

    def test_events_emitted(self):
        events = []

        class Sink:
            def cleanup_started(self, total):
                events.append(("started", total))

            def deleting(self, name):
                events.append(("deleting", name))

            def deleted(self, name):
                events.append(("deleted", name))

        api = _api(get_environment_by_name=self._env(1))
        asyncio.run(
            undeploy_environment("app", "default", api=api, events=Sink())
        )
        assert events == [
            ("started", 1),
            ("deleting", "svc-0"),
            ("deleted", "svc-0"),
        ]


class TestDeleteApp:
    def test_undeploys_all_envs_then_app(self):
        app_data = {
            "id": "app-1",
            "name": "myapp",
            "flashEnvironments": [
                {"id": "env-1", "name": "default"},
                {"id": "env-2", "name": "staging"},
            ],
        }
        api = _api(get_app_by_name=app_data)
        api.get_environment_by_name.side_effect = [
            {"id": "env-1", "name": "default", "endpoints": [{"id": "e1", "name": "a"}]},
            {"id": "env-2", "name": "staging", "endpoints": []},
        ]
        result = asyncio.run(delete_app("myapp", api=api))
        assert result.endpoints_deleted == 1
        assert result.app_deleted
        api.delete_app.assert_awaited_once_with("app-1")

    def test_failure_keeps_app(self):
        app_data = {
            "id": "app-1",
            "name": "myapp",
            "flashEnvironments": [{"id": "env-1", "name": "default"}],
        }
        api = _api(get_app_by_name=app_data)
        api.get_environment_by_name.return_value = {
            "id": "env-1",
            "name": "default",
            "endpoints": [{"id": "e1", "name": "a"}],
        }
        api.delete_endpoint.side_effect = RuntimeError("nope")
        result = asyncio.run(delete_app("myapp", api=api))
        assert not result.app_deleted
        assert result.failures
        api.delete_app.assert_not_awaited()
