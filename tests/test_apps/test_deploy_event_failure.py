"""reconciliation errors before endpoint operations begin."""

from unittest.mock import AsyncMock

import pytest

from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.deploy import reconcile_endpoints
from runpod.apps.errors import EndpointReconciliationError


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


@pytest.mark.parametrize("existing", [False, True])
async def test_phase_lookup_failure_preserves_original_error(existing):
    error = RuntimeError("phase handler unavailable")

    class Events:
        @property
        def phase(self):
            raise error

    app = App("demo")
    app.queue(name="worker", cpu="cpu3c-1-2")(lambda: None)
    api = AsyncMock()
    environment = {
        "id": "env",
        "name": "prod",
        "endpoints": [{"id": "ep-old", "name": "worker"}] if existing else [],
    }

    with pytest.raises(EndpointReconciliationError) as caught:
        await reconcile_endpoints(
            api, app, environment, "new-build", "3.12", events=Events()
        )

    assert str(caught.value) == "Couldn't prepare deployment: phase handler unavailable"
    assert caught.value.__cause__ is error
    assert api.mock_calls == []
