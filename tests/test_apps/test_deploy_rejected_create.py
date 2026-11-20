"""confirmed rejection and in-flight uncertainty during first deployment."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import aiohttp
import pytest

from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.deploy import reconcile_endpoints
from runpod.apps.errors import EndpointReconciliationError
from runpod.error import QueryError


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


@pytest.mark.parametrize(
    "error_type",
    [
        QueryError,
        RuntimeError,
        aiohttp.ClientConnectionError,
        OSError,
        TimeoutError,
        asyncio.TimeoutError,
        asyncio.CancelledError,
        KeyboardInterrupt,
    ],
)
@pytest.mark.parametrize("matches", [0, 1, 2])
async def test_create_failure_cleanup_depends_on_listing_and_error(
    error_type, matches, caplog
):
    app = App("demo")
    for name in ("a_ok", "b_bad"):
        app.queue(name=name, cpu="cpu3c-1-2")(lambda: None)
    environment = {"id": "env", "name": "prod", "endpoints": []}
    state = {**deepcopy(environment), "activeBuildId": "new-build"}
    api = AsyncMock()
    error = error_type("request failed")

    async def save(payload):
        if payload["name"] == "a_ok":
            state["endpoints"].append(
                {"id": "ep-ok", "name": "a_ok", "template": payload["template"]}
            )
            return {"id": "ep-ok"}
        for index in range(matches):
            state["endpoints"].append(
                {
                    "id": f"ep-bad-{index}",
                    "name": "b_bad",
                    "template": payload["template"],
                }
            )
        raise error

    async def delete(endpoint_id):
        state["endpoints"] = [
            item for item in state["endpoints"] if item["id"] != endpoint_id
        ]
        return True

    api.save_endpoint.side_effect = save
    api.delete_endpoint.side_effect = delete
    api.get_environment_by_name.side_effect = lambda *args: deepcopy(state)
    api.delete_environment.return_value = True
    interrupted = isinstance(error, (asyncio.CancelledError, KeyboardInterrupt))
    with pytest.raises(
        error_type if interrupted else EndpointReconciliationError
    ) as caught:
        await reconcile_endpoints(
            api,
            app,
            environment,
            "new-build",
            "3.12",
            activate_build=True,
            environment_created=True,
        )
    message = caplog.text if interrupted else str(caught.value)
    uncertain = matches > 1 or (
        matches == 0 and error_type not in (QueryError, RuntimeError)
    )
    if uncertain:
        api.delete_environment.assert_not_awaited()
        assert "b_bad: creation status unknown" in message
        assert "Rollback incomplete:" in message
        assert "Best-effort rollback completed." not in message
    else:
        api.delete_environment.assert_awaited_once_with("env")
        assert state["endpoints"] == []
        assert "Best-effort rollback completed." in message
        assert "creation status unknown" not in message
        assert "Remaining workers may still incur charges." not in message
    expected_deletes = [("ep-bad-0",), ("ep-ok",)] if matches == 1 else [("ep-ok",)]
    assert [
        call.args for call in api.delete_endpoint.await_args_list
    ] == expected_deletes
    if not interrupted:
        assert caught.value.__cause__ is error


@pytest.mark.parametrize("current", [None, {}, {"endpoints": None}, {"endpoints": {}}])
async def test_rejected_create_with_invalid_listing_remains_unknown(current):
    app = App("demo")
    app.queue(name="worker", cpu="cpu3c-1-2")(lambda: None)
    api = AsyncMock()
    api.save_endpoint.side_effect = QueryError("quota exceeded")
    api.get_environment_by_name.return_value = current
    with pytest.raises(EndpointReconciliationError, match="creation status unknown"):
        await reconcile_endpoints(
            api,
            app,
            {"id": "env", "name": "prod"},
            "new-build",
            "3.12",
            environment_created=True,
        )
    api.delete_endpoint.assert_not_awaited()
    api.delete_environment.assert_not_awaited()
