"""worker credentials: only resources that declare remote_calls get an api
key, minted per app environment, and templates only ever carry a secret
reference to it."""

import json
from unittest.mock import AsyncMock

import pytest

import runpod
from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.app_key import (
    APP_KEY_SCOPES,
    AppKeyError,
    app_key_name,
    delete_app_key,
    ensure_app_key,
    rotate_app_key,
)
from runpod.apps.deploy import _deployed_endpoint_input, reconcile_endpoints
from runpod.apps.dev import DevSession, _endpoint_input
from runpod.apps.manage import delete_app, undeploy_environment
from runpod.apps.secret import Secret
from runpod.error import QueryError

ACCOUNT_KEY = "rpa_fakeaccountkey0000000000000000"
APP_KEY = "rpa_fakeappkey00000000000000000000"
PROD_SECRET = "runpod-app-key.pipe.prod"
DEV_SECRET = "runpod-dev-key.pipe"


def _reference(name):
    return "{{ RUNPOD_SECRET_" + name + " }}"


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


@pytest.fixture
def logged_in(monkeypatch):
    for name in ("RUNPOD_ENDPOINT_ID", "RUNPOD_POD_ID", "RUNPOD_DEV_APP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RUNPOD_API_KEY", ACCOUNT_KEY)


def _api(secrets=None, keys=None):
    api = AsyncMock()
    api.list_secrets.return_value = list(secrets or [])
    api.list_api_keys.return_value = list(keys or [])
    api.create_api_key.return_value = {"id": "key-new", "rawKey": APP_KEY}
    api.list_my_endpoints.return_value = []
    api.save_endpoint.return_value = {"id": "ep-new"}
    return api


def _pipeline_app():
    app = App("pipe")

    @app.queue(name="leaf", cpu="cpu3c-1-2")
    def leaf(x: int):
        return x + 1

    @app.queue(name="root", cpu="cpu3c-1-2", remote_calls=True)
    def root(x: int):
        return leaf.remote(x)

    return app, leaf, root


def _env(payload):
    return {e["key"]: e["value"] for e in payload["template"]["env"]}


def _saved(api):
    return {
        call.args[0]["name"]: call.args[0] for call in api.save_endpoint.await_args_list
    }


def _no_key_values(*payloads):
    text = json.dumps(payloads)
    return ACCOUNT_KEY not in text and APP_KEY not in text


class TestAppKeyName:
    def test_environment_and_dev_names_differ(self):
        assert app_key_name("pipe", "prod") == PROD_SECRET
        assert app_key_name("pipe") == DEV_SECRET
        assert app_key_name("pipe", "dev") != app_key_name("pipe")

    def test_long_names_fit_api_key_limit_and_stay_distinct(self):
        a = app_key_name("a" * 80, "prod")
        b = app_key_name("a" * 80, "staging")
        assert len(a) <= 64 and len(b) <= 64
        assert a != b
        Secret(a)

    def test_unsafe_characters_become_valid_secret_names(self):
        Secret(app_key_name("my app", "prod/eu"))


class TestEnsureAppKey:
    async def test_mints_scoped_key_into_secret(self):
        api = _api()
        secret = await ensure_app_key(api, "pipe", "prod")
        assert secret.name == PROD_SECRET
        api.create_api_key.assert_awaited_once_with(PROD_SECRET, APP_KEY_SCOPES)
        args = api.create_secret.await_args
        assert args.args == (PROD_SECRET, APP_KEY)
        assert APP_KEY not in args.kwargs["description"]

    async def test_reuses_existing_secret_without_minting(self):
        api = _api([{"id": "s1", "name": PROD_SECRET}])
        assert (await ensure_app_key(api, "pipe", "prod")).name == PROD_SECRET
        api.create_api_key.assert_not_awaited()
        api.create_secret.assert_not_awaited()

    async def test_concurrent_creation_revokes_the_extra_key(self):
        api = _api()
        api.list_secrets.side_effect = [[], [{"id": "s1", "name": PROD_SECRET}]]
        api.create_secret.side_effect = QueryError("Secret name must be unique.")
        assert (await ensure_app_key(api, "pipe", "prod")).name == PROD_SECRET
        api.delete_api_key.assert_awaited_once_with("key-new")

    async def test_secret_failure_revokes_key_and_propagates(self):
        api = _api()
        api.create_secret.side_effect = QueryError("forbidden")
        with pytest.raises(QueryError):
            await ensure_app_key(api, "pipe", "prod")
        api.delete_api_key.assert_awaited_once_with("key-new")

    async def test_key_creation_failure_propagates_before_any_secret(self):
        api = _api()
        api.create_api_key.side_effect = QueryError("max of 50 API keys are allowed")
        with pytest.raises(QueryError):
            await ensure_app_key(api, "pipe", "prod")
        api.create_secret.assert_not_awaited()


class TestRotateAppKey:
    async def test_new_key_replaces_secret_value_then_old_keys_are_revoked(self):
        api = _api(
            [{"id": "s1", "name": PROD_SECRET}],
            [{"id": "key-old", "name": PROD_SECRET}, {"id": "other", "name": "mine"}],
        )
        await rotate_app_key(api, "pipe", "prod")
        api.update_secret_value.assert_awaited_once_with("s1", APP_KEY)
        api.delete_api_key.assert_awaited_once_with("key-old")

    async def test_missing_secret_is_an_error(self):
        api = _api()
        with pytest.raises(AppKeyError):
            await rotate_app_key(api, "pipe", "prod")
        api.create_api_key.assert_not_awaited()


class TestDeleteAppKey:
    async def test_removes_secret_and_every_key_with_its_name(self):
        api = _api(
            [{"id": "s1", "name": PROD_SECRET}],
            [
                {"id": "k1", "name": PROD_SECRET},
                {"id": "k2", "name": PROD_SECRET},
                {"id": "k3", "name": "mine"},
            ],
        )
        assert await delete_app_key(api, "pipe", "prod")
        api.delete_secret.assert_awaited_once_with("s1")
        assert [c.args[0] for c in api.delete_api_key.await_args_list] == ["k1", "k2"]

    async def test_nothing_to_delete(self):
        api = _api()
        assert not await delete_app_key(api, "pipe", "prod")
        api.delete_secret.assert_not_awaited()

    async def test_environment_deletion_removes_its_key(self):
        api = _api([{"id": "s1", "name": PROD_SECRET}])
        api.get_environment_by_name.return_value = {"id": "env-1", "endpoints": []}
        result = await undeploy_environment("pipe", "prod", api=api)
        assert result.environment_deleted and not result.failures
        api.delete_secret.assert_awaited_once_with("s1")

    async def test_kept_environment_keeps_its_key(self):
        api = _api([{"id": "s1", "name": PROD_SECRET}])
        api.get_environment_by_name.return_value = {"id": "env-1", "endpoints": []}
        await undeploy_environment("pipe", "prod", api=api, delete_env=False)
        api.delete_secret.assert_not_awaited()

    async def test_app_deletion_removes_environment_and_dev_keys(self):
        api = _api(
            [{"id": "s1", "name": PROD_SECRET}, {"id": "s2", "name": DEV_SECRET}]
        )
        api.get_app_by_name.return_value = {
            "id": "app-1",
            "flashEnvironments": [{"name": "prod"}],
        }
        api.get_environment_by_name.return_value = {"id": "env-1", "endpoints": []}
        result = await delete_app("pipe", api=api)
        assert result.app_deleted and not result.failures
        assert {c.args[0] for c in api.delete_secret.await_args_list} == {"s1", "s2"}


class TestDeployedEndpoints:
    def test_undeclared_resource_gets_no_key(self, logged_in, monkeypatch):
        monkeypatch.setattr(runpod, "api_key", ACCOUNT_KEY)
        app, leaf, _ = _pipeline_app()
        payload = _deployed_endpoint_input(
            app, leaf.spec, "env-1", "build-1", "3.12", app_key=Secret(PROD_SECRET)
        )
        assert "RUNPOD_API_KEY" not in _env(payload)
        assert _no_key_values(payload)

    async def test_declared_resource_references_environment_key(self, logged_in):
        app, _, _ = _pipeline_app()
        api = _api()
        await reconcile_endpoints(
            api, app, {"id": "env-1", "name": "prod"}, "build-1", "3.12"
        )
        saved = _saved(api)
        assert _env(saved["root"])["RUNPOD_API_KEY"] == _reference(PROD_SECRET)
        assert "RUNPOD_API_KEY" not in _env(saved["leaf"])
        assert _no_key_values(*saved.values())
        api.create_api_key.assert_awaited_once()

    async def test_redeploy_reuses_the_environment_key(self, logged_in):
        app, _, _ = _pipeline_app()
        api = _api([{"id": "s1", "name": PROD_SECRET}])
        await reconcile_endpoints(
            api, app, {"id": "env-1", "name": "prod"}, "build-2", "3.12"
        )
        assert _env(_saved(api)["root"])["RUNPOD_API_KEY"] == _reference(PROD_SECRET)
        api.create_api_key.assert_not_awaited()

    async def test_no_declared_resource_never_touches_keys(self, logged_in):
        app = App("solo")

        @app.queue(name="only", cpu="cpu3c-1-2")
        def only():
            pass

        api = _api()
        await reconcile_endpoints(
            api, app, {"id": "env-1", "name": "default"}, "build-1", "3.12"
        )
        assert "RUNPOD_API_KEY" not in _env(_saved(api)["only"])
        api.list_secrets.assert_not_awaited()
        api.create_api_key.assert_not_awaited()

    async def test_explicit_env_credentials_win(self, logged_in):
        app = App("own")

        @app.queue(
            name="caller",
            cpu="cpu3c-1-2",
            remote_calls=True,
            env={"RUNPOD_API_KEY": Secret("my-scoped-key")},
        )
        def caller():
            pass

        api = _api()
        await reconcile_endpoints(
            api, app, {"id": "env-1", "name": "default"}, "build-1", "3.12"
        )
        env = _env(_saved(api)["caller"])
        assert env["RUNPOD_API_KEY"] == _reference("my-scoped-key")
        api.create_api_key.assert_not_awaited()


class TestDevEndpoints:
    def test_payload_needs_no_credentials(self, monkeypatch):
        monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
        monkeypatch.setattr(runpod, "api_key", None)
        app, _, root = _pipeline_app()
        assert "RUNPOD_API_KEY" not in _env(_endpoint_input(app, root.spec))

    async def test_session_references_dev_key_only_for_declared(self, logged_in):
        app, _, _ = _pipeline_app()
        api = _api()
        session = DevSession([app], api=api)
        await session.start()
        await session.refresh([app])
        saved = {
            call.args[0]["name"].split("-")[2]: call.args[0]
            for call in api.save_endpoint.await_args_list
        }
        assert _env(saved["root"])["RUNPOD_API_KEY"] == _reference(DEV_SECRET)
        assert "RUNPOD_API_KEY" not in _env(saved["leaf"])
        assert _no_key_values([c.args for c in api.save_endpoint.await_args_list])
        api.create_api_key.assert_awaited_once()

    async def test_session_without_declared_never_touches_keys(self, logged_in):
        app = App("solo")

        @app.queue(name="only", cpu="cpu3c-1-2")
        def only():
            pass

        api = _api()
        await DevSession([app], api=api).start()
        api.list_secrets.assert_not_awaited()
        api.create_api_key.assert_not_awaited()


class TestNestedCallsInWorker:
    async def test_declared_worker_authenticates_with_substituted_key(
        self, logged_in, monkeypatch
    ):
        app, leaf, _ = _pipeline_app()
        api = _api()
        await reconcile_endpoints(
            api, app, {"id": "env-1", "name": "prod"}, "build-1", "3.12"
        )
        root_env = _env(_saved(api)["root"])
        assert root_env["RUNPOD_API_KEY"] == _reference(PROD_SECRET)

        # the platform decrypts the secret reference when the worker boots
        monkeypatch.setenv("RUNPOD_API_KEY", APP_KEY)
        monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep-root")
        monkeypatch.setenv("FLASH_ENVIRONMENT", root_env["FLASH_ENVIRONMENT"])
        target = await app._resolve(leaf.spec)
        headers = target._sentinel_headers()
        assert headers["Authorization"] == f"Bearer {APP_KEY}"
        assert headers["X-Flash-Endpoint"] == "leaf"
        assert headers["X-Flash-Environment"] == "prod"

    async def test_undeclared_worker_fails_with_declaration_hint(self, monkeypatch):
        monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
        monkeypatch.delenv("RUNPOD_DEV_APP", raising=False)
        monkeypatch.setattr(runpod, "api_key", None)
        monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep-leaf")
        app, leaf, _ = _pipeline_app()
        target = await app._resolve(leaf.spec)
        with pytest.raises(RuntimeError, match="remote_calls=True"):
            target._sentinel_headers()
