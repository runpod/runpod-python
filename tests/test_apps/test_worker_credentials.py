"""worker credentials: workers get an api key minted per app environment,
and templates only ever carry a secret reference to it."""

import asyncio
import json
from unittest.mock import AsyncMock, call

import aiohttp
import pytest

import runpod
from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.app_key import (
    APP_KEY_SCOPES,
    app_key_name,
    delete_app_key,
    ensure_app_key,
)
from runpod.apps.deploy import _deployed_endpoint_input, reconcile_endpoints
from runpod.apps.dev import DevSession, _endpoint_input
from runpod.apps.errors import AppError
from runpod.apps.manage import delete_app, undeploy_environment
from runpod.apps.secret import Secret
from runpod.error import AuthenticationError, QueryError

ACCOUNT_KEY = "rpa_fakeaccountkey0000000000000000"
APP_KEY = "rpa_fakeappkey00000000000000000000"
PROD_SECRET = "flash-app-key.pipe.prod.ab680dbced7cc40bff0314e6"
DEV_SECRET = "flash-dev-key.pipe.8afde8d00cfa7b4458c20c7d"


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

    @app.queue(name="root", cpu="cpu3c-1-2")
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

    @pytest.mark.parametrize(
        "left,right",
        [
            (("my app", "prod"), ("my-app", "prod")),
            (("a.b", "c"), ("a", "b.c")),
            (("MyApp", "prod"), ("myapp", "prod")),
            (("pipe", "Prod"), ("pipe", "prod")),
            (("MyApp", None), ("myapp", None)),
            (("pipe", None), ("pipe", "")),
        ],
    )
    def test_original_identities_are_distinct_case_insensitively(self, left, right):
        first, second = app_key_name(*left), app_key_name(*right)
        assert first.casefold() != second.casefold()
        assert first == app_key_name(*left)
        assert len(first) <= 64 and len(second) <= 64
        Secret(first)
        Secret(second)


class TestEnsureAppKey:

    @pytest.mark.parametrize(
        "outcome", ["absent", "committed", "winner", "unknown", "revoke_failed"]
    )
    async def test_cancelled_secret_creation_reconciles_and_propagates(
        self, outcome, caplog
    ):
        api = _api()
        entered = asyncio.Event()
        blocked = asyncio.Event()

        async def create_secret(name, value, description):
            if outcome in ("committed", "winner"):
                api.list_secrets.return_value = [
                    {
                        "id": "s1",
                        "name": name,
                        "description": (
                            description if outcome == "committed" else "another owner"
                        ),
                    }
                ]
                api.list_api_keys.return_value = [{"id": "key-new", "name": name}]
            elif outcome == "unknown":
                api.list_secrets.side_effect = AuthenticationError(APP_KEY)
            elif outcome == "revoke_failed":
                api.delete_api_key.side_effect = RuntimeError(APP_KEY)
            entered.set()
            await blocked.wait()

        api.create_secret.side_effect = create_secret
        task = asyncio.create_task(ensure_app_key(api, "pipe", "prod"))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            if outcome in ("committed", "unknown"):
                api.delete_api_key.assert_not_awaited()
            else:
                api.delete_api_key.assert_awaited_once_with("key-new")
            assert APP_KEY not in caplog.text
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_base_exception_after_key_creation_runs_rollback(self):
        api = _api()
        failure = BaseException("interrupted")
        api.create_secret.side_effect = failure
        with pytest.raises(BaseException) as caught:
            await ensure_app_key(api, "pipe", "prod")
        assert caught.value is failure
        api.delete_api_key.assert_awaited_once_with("key-new")

    async def test_concurrent_stale_secret_refresh_reconciles_one_live_pair(self):
        api = _api()
        stale = {
            "id": "stale",
            "name": PROD_SECRET,
            "description": "api key revoked for pipe/prod workers",
        }
        secret = stale
        keys = {}
        reads = 0
        both_read = asyncio.Event()

        async def list_secrets(name):
            nonlocal reads
            if reads < 2:
                reads += 1
                if reads == 2:
                    both_read.set()
                await both_read.wait()
                return [stale]
            return [secret] if secret else []

        async def list_keys():
            return [{"id": key_id, "name": PROD_SECRET} for key_id in keys]

        async def delete_secret(secret_id):
            nonlocal secret
            if secret is None or secret["id"] != secret_id:
                raise QueryError("not found", status_code=404)
            secret = None

        async def create_key(name, scopes):
            key_id = f"key-{api.create_api_key.await_count}"
            keys[key_id] = APP_KEY
            return {"id": key_id, "rawKey": APP_KEY}

        async def create_secret(name, value, description):
            nonlocal secret
            if secret is not None:
                raise QueryError("conflict", status_code=409)
            secret = {"id": "fresh", "name": name, "description": description}

        async def delete_key(key_id):
            del keys[key_id]

        api.list_secrets.side_effect = list_secrets
        api.list_api_keys.side_effect = list_keys
        api.delete_secret.side_effect = delete_secret
        api.create_api_key.side_effect = create_key
        api.create_secret.side_effect = create_secret
        api.delete_api_key.side_effect = delete_key
        results = await asyncio.wait_for(
            asyncio.gather(*(ensure_app_key(api, "pipe", "prod") for _ in range(2))),
            2,
        )
        assert [result.name for result in results] == [PROD_SECRET, PROD_SECRET]
        assert api.delete_secret.await_count == 2
        assert api.create_api_key.await_count == 2
        assert len(keys) == 1
        assert (
            secret["description"] == f"api key {next(iter(keys))} for pipe/prod workers"
        )

    @pytest.mark.parametrize("status", [403, 500])
    async def test_stale_secret_delete_errors_other_than_missing_propagate(
        self, status
    ):
        api = _api([{"id": "stale", "name": PROD_SECRET}])
        failure = QueryError("delete failed", status_code=status)
        api.delete_secret.side_effect = failure
        with pytest.raises(QueryError) as caught:
            await ensure_app_key(api, "pipe", "prod")
        assert caught.value is failure
        api.create_api_key.assert_not_awaited()

    async def test_refresh_preserves_other_keys_until_teardown(self):
        api = _api(
            [
                {
                    "id": "stale",
                    "name": PROD_SECRET,
                    "description": "api key revoked for pipe/prod workers",
                }
            ],
            [{"id": "orphan", "name": PROD_SECRET}],
        )
        await ensure_app_key(api, "pipe", "prod")
        api.delete_api_key.assert_not_awaited()
        api.list_secrets.return_value = [{"id": "fresh", "name": PROD_SECRET}]
        api.list_api_keys.return_value.append({"id": "key-new", "name": PROD_SECRET})
        await delete_app_key(api, "pipe", "prod")
        assert api.delete_api_key.await_args_list == [call("orphan"), call("key-new")]

    @pytest.mark.parametrize(
        "message,status",
        [
            ("max of 50 API keys are allowed", None),
            ("rate limit exceeded", 429),
            ("permission service unavailable", 503),
            ("upstream error", 500),
            ("invalid scope", 400),
        ],
    )
    async def test_nonpermission_key_errors_are_unchanged(self, message, status):
        api = _api()
        failure = QueryError(message, status_code=status, errors=["detail"])
        api.create_api_key.side_effect = failure
        with pytest.raises(QueryError) as caught:
            await ensure_app_key(api, "pipe", "prod")
        assert caught.value is failure
        assert str(caught.value) == message
        assert caught.value.errors == ["detail"]
        api.create_secret.assert_not_awaited()

    @pytest.mark.parametrize(
        "message,status",
        [("denied", 403), ("unauthorized", 401), ("permission denied", None)],
    )
    async def test_permission_query_errors_include_override_guidance(
        self, message, status
    ):
        api = _api()
        api.create_api_key.side_effect = QueryError(message, status_code=status)
        with pytest.raises(QueryError, match="RUNPOD_API_KEY"):
            await ensure_app_key(api, "pipe", "prod")

    async def test_mints_scoped_key_into_secret(self):
        api = _api()
        secret = await ensure_app_key(api, "pipe", "prod")
        assert secret.name == PROD_SECRET
        api.create_api_key.assert_awaited_once_with(PROD_SECRET, APP_KEY_SCOPES)
        args = api.create_secret.await_args
        assert args.args == (PROD_SECRET, APP_KEY)
        assert APP_KEY not in args.kwargs["description"]

    async def test_reuses_existing_secret_without_minting(self):
        api = _api(
            [{"id": "s1", "name": PROD_SECRET}],
            [{"id": "k1", "name": PROD_SECRET}],
        )
        assert (await ensure_app_key(api, "pipe", "prod")).name == PROD_SECRET
        api.create_api_key.assert_not_awaited()
        api.create_secret.assert_not_awaited()

    async def test_revoked_key_refreshes_stale_secret(self):
        api = _api(
            [{"id": "s1", "name": PROD_SECRET}],
            [{"id": "other", "name": "unrelated"}],
        )
        assert (await ensure_app_key(api, "pipe", "prod")).name == PROD_SECRET
        api.delete_secret.assert_awaited_once_with("s1")
        api.create_api_key.assert_awaited_once_with(PROD_SECRET, APP_KEY_SCOPES)
        api.create_secret.assert_awaited_once()
        assert api.mock_calls.index(call.delete_secret("s1")) < api.mock_calls.index(
            call.create_api_key(PROD_SECRET, APP_KEY_SCOPES)
        )

    @pytest.mark.parametrize("owner_live", [True, False])
    async def test_existing_secret_validates_recorded_owner_not_retained_orphan(
        self, owner_live
    ):
        keys = [{"id": "orphan", "name": PROD_SECRET}]
        if owner_live:
            keys.append({"id": "owner", "name": PROD_SECRET})
        api = _api(
            [
                {
                    "id": "s1",
                    "name": PROD_SECRET,
                    "description": "api key owner for pipe/prod workers",
                }
            ],
            keys,
        )
        assert (await ensure_app_key(api, "pipe", "prod")).name == PROD_SECRET
        if owner_live:
            api.delete_secret.assert_not_awaited()
            api.create_api_key.assert_not_awaited()
        else:
            api.delete_secret.assert_awaited_once_with("s1")
            api.create_api_key.assert_awaited_once_with(PROD_SECRET, APP_KEY_SCOPES)
            api.create_secret.assert_awaited_once()

    async def test_legacy_secret_description_uses_same_name_key_check(self):
        api = _api(
            [
                {
                    "id": "s1",
                    "name": PROD_SECRET,
                    "description": "api key for pipe/prod workers",
                }
            ],
            [{"id": "owner", "name": PROD_SECRET}],
        )
        assert (await ensure_app_key(api, "pipe", "prod")).name == PROD_SECRET
        api.delete_secret.assert_not_awaited()
        api.create_api_key.assert_not_awaited()

    async def test_stale_secret_lookup_failure_does_not_rotate_credentials(self):
        api = _api([{"id": "s1", "name": PROD_SECRET}])
        api.list_api_keys.side_effect = AuthenticationError("denied")
        with pytest.raises(AuthenticationError):
            await ensure_app_key(api, "pipe", "prod")
        api.delete_secret.assert_not_awaited()
        api.create_api_key.assert_not_awaited()

    async def test_concurrent_creation_revokes_the_extra_key(self):
        api = _api(keys=[{"id": "winner", "name": PROD_SECRET}])
        api.list_secrets.side_effect = [[], [{"id": "s1", "name": PROD_SECRET}]]
        api.create_secret.side_effect = QueryError(
            "Secret name must be unique.", status_code=409
        )
        assert (await ensure_app_key(api, "pipe", "prod")).name == PROD_SECRET
        api.delete_api_key.assert_awaited_once_with("key-new")

    @pytest.mark.parametrize("owner_live", [True, False])
    async def test_concurrent_winner_requires_its_recorded_live_key(self, owner_live):
        keys = [
            {"id": "key-new", "name": PROD_SECRET},
            {"id": "orphan", "name": PROD_SECRET},
        ]
        if owner_live:
            keys.append({"id": "winner", "name": PROD_SECRET})
        api = _api(keys=keys)
        failure = QueryError("conflict", status_code=409)
        api.create_secret.side_effect = failure
        api.delete_api_key.side_effect = RuntimeError("revocation failed")
        api.list_secrets.side_effect = [
            [],
            [
                {
                    "id": "s1",
                    "name": PROD_SECRET,
                    "description": "api key winner for pipe/prod workers",
                }
            ],
        ]
        if owner_live:
            assert (await ensure_app_key(api, "pipe", "prod")).name == PROD_SECRET
        else:
            with pytest.raises(QueryError) as caught:
                await ensure_app_key(api, "pipe", "prod")
            assert caught.value is failure
        api.delete_api_key.assert_awaited_once_with("key-new")
        api.delete_secret.assert_not_awaited()

    async def test_metadata_less_winner_cannot_use_failed_rollback_key(self):
        api = _api(keys=[{"id": "key-new", "name": PROD_SECRET}])
        failure = QueryError("conflict", status_code=409)
        api.create_secret.side_effect = failure
        api.delete_api_key.side_effect = RuntimeError("revocation failed")
        api.list_secrets.side_effect = [[], [{"id": "s1", "name": PROD_SECRET}]]
        with pytest.raises(QueryError) as caught:
            await ensure_app_key(api, "pipe", "prod")
        assert caught.value is failure

    async def test_concurrent_winner_key_lookup_failure_preserves_original_error(self):
        api = _api()
        failure = QueryError("conflict", status_code=409)
        api.create_secret.side_effect = failure
        api.list_secrets.side_effect = [
            [],
            [
                {
                    "id": "s1",
                    "name": PROD_SECRET,
                    "description": "api key winner for pipe/prod workers",
                }
            ],
        ]
        api.list_api_keys.side_effect = AuthenticationError("read denied")
        with pytest.raises(QueryError) as caught:
            await ensure_app_key(api, "pipe", "prod")
        assert caught.value is failure
        api.delete_api_key.assert_awaited_once_with("key-new")

    @pytest.mark.parametrize(
        "failure",
        [
            AuthenticationError("denied"),
            aiohttp.ClientConnectionError("disconnected"),
            TimeoutError("timed out"),
            OSError("unreachable"),
        ],
    )
    async def test_secret_transport_failure_revokes_unstored_key(self, failure):
        api = _api()
        api.create_secret.side_effect = failure
        with pytest.raises(type(failure)) as caught:
            await ensure_app_key(api, "pipe", "prod")
        assert caught.value is failure
        api.delete_api_key.assert_awaited_once_with("key-new")

    @pytest.mark.parametrize(
        "failure", [TimeoutError("timed out"), QueryError("failed", status_code=500)]
    )
    async def test_committed_secret_keeps_its_key_after_lost_response(self, failure):
        api = _api(keys=[{"id": "key-new", "name": PROD_SECRET}])

        async def commit(name, value, description):
            api.list_secrets.return_value = [
                {"id": "s1", "name": name, "description": description}
            ]
            raise failure

        api.create_secret.side_effect = commit
        assert (await ensure_app_key(api, "pipe", "prod")).name == PROD_SECRET
        api.delete_api_key.assert_not_awaited()

    async def test_committed_secret_with_revoked_owner_is_not_reused(self):
        api = _api(keys=[{"id": "orphan", "name": PROD_SECRET}])
        failure = TimeoutError("post response lost")
        api.create_secret.side_effect = failure
        api.list_secrets.side_effect = [
            [],
            [
                {
                    "id": "s1",
                    "name": PROD_SECRET,
                    "description": "api key key-new for pipe/prod workers",
                }
            ],
        ]
        with pytest.raises(TimeoutError) as caught:
            await ensure_app_key(api, "pipe", "prod")
        assert caught.value is failure
        api.delete_api_key.assert_not_awaited()

    async def test_uncertain_secret_outcome_preserves_key_and_original_error(self):
        api = _api()
        failure = TimeoutError("post response lost")
        api.create_secret.side_effect = failure
        api.list_secrets.side_effect = [[], AuthenticationError("read denied")]
        with pytest.raises(TimeoutError) as caught:
            await ensure_app_key(api, "pipe", "prod")
        assert caught.value is failure
        api.delete_api_key.assert_not_awaited()

    async def test_uncertain_secret_metadata_does_not_revoke_its_possible_key(self):
        api = _api()
        failure = TimeoutError("post response lost")
        api.create_secret.side_effect = failure
        api.list_secrets.side_effect = [[], [{"id": "s1", "name": PROD_SECRET}]]
        with pytest.raises(TimeoutError) as caught:
            await ensure_app_key(api, "pipe", "prod")
        assert caught.value is failure
        api.delete_api_key.assert_not_awaited()

    @pytest.mark.parametrize("winner", [True, False])
    async def test_revoke_failure_does_not_mask_creation_result(self, winner, caplog):
        api = _api(keys=[{"id": "winner", "name": PROD_SECRET}] if winner else [])
        failure = QueryError("conflict", status_code=409)
        api.create_secret.side_effect = failure
        api.delete_api_key.side_effect = RuntimeError(APP_KEY)
        api.list_secrets.side_effect = [
            [],
            (
                [{"id": "s1", "name": PROD_SECRET, "description": "another key"}]
                if winner
                else []
            ),
        ]
        if winner:
            assert (await ensure_app_key(api, "pipe", "prod")).name == PROD_SECRET
        else:
            with pytest.raises(QueryError) as caught:
                await ensure_app_key(api, "pipe", "prod")
            assert caught.value is failure
        api.delete_api_key.assert_awaited_once_with("key-new")
        assert "could not revoke api key for secret" in caplog.text
        assert "key-new" not in caplog.text
        assert APP_KEY not in caplog.text

    @pytest.mark.parametrize("outcome", ["uncertain", "metadata_less", "revoke_failed"])
    async def test_cleanup_logs_exclude_key_response_fields(self, outcome, caplog):
        api = _api()
        api.create_api_key.return_value = {"id": APP_KEY, "rawKey": APP_KEY}
        api.create_secret.side_effect = TimeoutError(APP_KEY)
        if outcome == "uncertain":
            api.list_secrets.side_effect = [[], AuthenticationError(APP_KEY)]
        elif outcome == "metadata_less":
            api.list_secrets.side_effect = [[], [{"id": "s1", "name": PROD_SECRET}]]
        else:
            api.delete_api_key.side_effect = RuntimeError(APP_KEY)
        with pytest.raises(TimeoutError):
            await ensure_app_key(api, "pipe", "prod")
        assert PROD_SECRET in caplog.text
        assert APP_KEY not in caplog.text

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

    @pytest.mark.parametrize(
        "failure",
        [
            QueryError("Scope(s) not grantable by the caller"),
            AuthenticationError("denied"),
        ],
    )
    async def test_key_permission_error_explains_explicit_credentials(self, failure):
        api = _api()
        api.create_api_key.side_effect = failure
        with pytest.raises(type(failure), match="RUNPOD_API_KEY") as caught:
            await ensure_app_key(api, "pipe", "prod")
        assert "each resource's env" in str(caught.value)
        assert "API key creation permission" in str(caught.value)
        assert all(scope in str(caught.value) for scope in APP_KEY_SCOPES)
        api.create_secret.assert_not_awaited()


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

    @pytest.mark.parametrize(
        "operation",
        ["list_secrets", "delete_secret", "list_api_keys", "delete_api_key"],
    )
    async def test_environment_survives_credential_failure_for_retry(self, operation):
        api = _api(
            [{"id": "s1", "name": PROD_SECRET}],
            [{"id": "k1", "name": PROD_SECRET}],
        )
        api.get_environment_by_name.return_value = {
            "id": "env-1",
            "endpoints": [{"id": "ep-1", "name": "worker"}],
        }
        getattr(api, operation).side_effect = RuntimeError("unavailable")
        result = await undeploy_environment("pipe", "prod", api=api)
        assert result.endpoints_deleted == 1
        assert result.failures and not result.environment_deleted
        api.delete_environment.assert_not_awaited()
        getattr(api, operation).side_effect = None
        result = await undeploy_environment("pipe", "prod", api=api)
        assert result.environment_deleted and not result.failures
        assert api.mock_calls.index(
            call.delete_endpoint("ep-1")
        ) < api.mock_calls.index(call.delete_secret("s1"))
        assert api.mock_calls.index(call.delete_api_key("k1")) < api.mock_calls.index(
            call.delete_environment("env-1")
        )

    @pytest.mark.parametrize(
        "operation",
        ["list_secrets", "delete_secret", "list_api_keys", "delete_api_key"],
    )
    async def test_app_survives_dev_credential_failure_for_retry(self, operation):
        api = _api(
            [{"id": "s1", "name": DEV_SECRET}],
            [{"id": "k1", "name": DEV_SECRET}],
        )
        api.get_app_by_name.return_value = {"id": "app-1", "flashEnvironments": []}
        getattr(api, operation).side_effect = RuntimeError("unavailable")
        result = await delete_app("pipe", api=api)
        assert result.failures and not result.app_deleted
        api.delete_app.assert_not_awaited()
        getattr(api, operation).side_effect = None
        result = await delete_app("pipe", api=api)
        assert result.app_deleted and not result.failures
        assert api.mock_calls.index(call.delete_api_key("k1")) < api.mock_calls.index(
            call.delete_app("app-1")
        )


class TestDeployedEndpoints:
    def test_account_key_never_lands_in_payload(self, logged_in, monkeypatch):
        monkeypatch.setattr(runpod, "api_key", ACCOUNT_KEY)
        app, leaf, _ = _pipeline_app()
        payload = _deployed_endpoint_input(app, leaf.spec, "env-1", "build-1", "3.12")
        assert "RUNPOD_API_KEY" not in _env(payload)
        assert _no_key_values(payload)

    async def test_every_resource_references_environment_key(self, logged_in):
        app, _, _ = _pipeline_app()
        api = _api()
        await reconcile_endpoints(
            api, app, {"id": "env-1", "name": "prod"}, "build-1", "3.12"
        )
        saved = _saved(api)
        assert _env(saved["root"])["RUNPOD_API_KEY"] == _reference(PROD_SECRET)
        assert _env(saved["leaf"])["RUNPOD_API_KEY"] == _reference(PROD_SECRET)
        assert _no_key_values(*saved.values())
        api.create_api_key.assert_awaited_once()

    async def test_redeploy_reuses_the_environment_key(self, logged_in):
        app, _, _ = _pipeline_app()
        api = _api(
            [{"id": "s1", "name": PROD_SECRET}],
            [{"id": "k1", "name": PROD_SECRET}],
        )
        await reconcile_endpoints(
            api, app, {"id": "env-1", "name": "prod"}, "build-2", "3.12"
        )
        assert _env(_saved(api)["root"])["RUNPOD_API_KEY"] == _reference(PROD_SECRET)
        api.create_api_key.assert_not_awaited()

    async def test_explicit_env_credentials_win(self, logged_in):
        app = App("own")

        @app.queue(
            name="caller",
            cpu="cpu3c-1-2",
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
        api.list_secrets.assert_not_awaited()
        api.create_api_key.assert_not_awaited()


class TestDevEndpoints:

    @pytest.mark.parametrize("cancelled", [False, True])
    async def test_start_failure_survives_credential_cleanup_failure(
        self, cancelled, caplog
    ):
        app, _, _ = _pipeline_app()
        api = _api(
            [{"id": "s1", "name": DEV_SECRET}],
            [{"id": "k1", "name": DEV_SECRET}],
        )
        failure = (
            asyncio.CancelledError("interrupted")
            if cancelled
            else RuntimeError("provisioning failed")
        )
        api.save_endpoint.side_effect = failure
        api.delete_api_key.side_effect = QueryError(APP_KEY, status_code=503)
        session = DevSession([app], api=api)
        with pytest.raises(type(failure)) as caught:
            async with session:
                pytest.fail("startup must fail")
        assert caught.value is failure
        assert "QueryError" in caplog.text
        assert "failed to clean up dev session" in caplog.text
        assert APP_KEY not in caplog.text
        assert session._app_keys
        api.delete_api_key.side_effect = None
        await session.stop()
        assert not session._app_keys

    async def test_credential_cleanup_reports_type_without_sensitive_message(
        self, caplog
    ):
        app, _, _ = _pipeline_app()
        api = _api(
            [{"id": "s1", "name": DEV_SECRET}],
            [{"id": "k1", "name": DEV_SECRET}],
        )
        session = DevSession([app], api=api)
        await session.start()
        api.delete_api_key.side_effect = QueryError(APP_KEY, status_code=503)
        with pytest.raises(
            AppError, match=r"credentials for pipe \(QueryError\)"
        ) as caught:
            await session.stop()
        assert APP_KEY not in str(caught.value)
        assert APP_KEY not in caplog.text

    async def test_stop_deletes_credentials_without_a_registered_app(self):
        app, _, _ = _pipeline_app()
        api = _api(
            [{"id": "s1", "name": DEV_SECRET}],
            [{"id": "k1", "name": DEV_SECRET}],
        )
        api.get_app_by_name.return_value = None
        session = DevSession([app], api=api)
        await session.start()
        await session.stop()
        api.delete_secret.assert_awaited_once_with("s1")
        api.delete_api_key.assert_awaited_once_with("k1")
        api.get_app_by_name.assert_not_awaited()
        assert api.mock_calls.index(
            call.delete_endpoint("ep-new")
        ) < api.mock_calls.index(call.delete_secret("s1"))
        await session.stop()
        api.delete_api_key.assert_awaited_once()

    async def test_failed_endpoint_teardown_retains_dev_credentials_for_retry(self):
        app, _, _ = _pipeline_app()
        api = _api()
        session = DevSession([app], api=api)
        await session.start()
        api.delete_endpoint.side_effect = RuntimeError("unavailable")
        with pytest.raises(AppError, match="dev cleanup incomplete"):
            await session.stop()
        api.delete_secret.assert_not_awaited()
        api.delete_api_key.assert_not_awaited()
        assert session._app_keys
        api.delete_endpoint.side_effect = None
        await session.stop()
        assert not session._app_keys

    async def test_failed_credential_teardown_can_be_retried(self):
        app, _, _ = _pipeline_app()
        api = _api(
            [{"id": "s1", "name": DEV_SECRET}],
            [{"id": "k1", "name": DEV_SECRET}],
        )
        session = DevSession([app], api=api)
        await session.start()
        await session.refresh([])
        api.delete_api_key.side_effect = RuntimeError("unavailable")
        with pytest.raises(AppError, match="credentials for pipe"):
            await session.stop()
        assert session._app_keys
        api.delete_api_key.side_effect = None
        await session.stop()
        assert not session._app_keys

    async def test_failed_start_cleans_up_credentials(self):
        app, _, _ = _pipeline_app()
        api = _api(
            [{"id": "s1", "name": DEV_SECRET}],
            [{"id": "k1", "name": DEV_SECRET}],
        )
        api.save_endpoint.side_effect = RuntimeError("provisioning failed")
        with pytest.raises(RuntimeError, match="provisioning failed"):
            async with DevSession([app], api=api):
                pass
        api.delete_secret.assert_awaited_once_with("s1")
        api.delete_api_key.assert_awaited_once_with("k1")

    def test_payload_needs_no_credentials(self, monkeypatch):
        monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
        monkeypatch.setattr(runpod, "api_key", None)
        app, _, root = _pipeline_app()
        assert "RUNPOD_API_KEY" not in _env(_endpoint_input(app, root.spec))

    async def test_session_references_dev_key(self, logged_in):
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
        assert _env(saved["leaf"])["RUNPOD_API_KEY"] == _reference(DEV_SECRET)
        assert _no_key_values([c.args for c in api.save_endpoint.await_args_list])
        api.create_api_key.assert_awaited_once()


class TestNestedCallsInWorker:
    async def test_worker_authenticates_with_substituted_key(
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
