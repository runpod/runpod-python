"""rollback diagnostics during asyncio runner shutdown."""

import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.deploy import reconcile_endpoints


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="requires asyncio.Runner SIGINT handling"
)
@pytest.mark.parametrize("interrupt_cleanup", [False, True])
def test_sigint_reports_rollback_progress(interrupt_cleanup):
    script = textwrap.dedent("""
        import asyncio
        import logging
        import signal
        import sys
        from unittest.mock import AsyncMock

        from runpod.apps import App
        from runpod.apps.deploy import reconcile_endpoints

        logging.basicConfig(level=logging.WARNING)
        app = App("demo")
        names = ("alpha", "beta", "gamma")
        for name in names:
            app.queue(name=name, cpu="cpu3c-1-2")(lambda: None)
        api = AsyncMock()
        api.snapshot_endpoint.side_effect = [
            {"id": f"ep-{name}", "name": name} for name in names
        ]
        api.get_environment_by_name.return_value = {"activeBuildId": "new-build"}

        async def save(payload):
            if "template" in payload:
                if payload["name"] == "gamma":
                    asyncio.get_running_loop().call_soon(signal.raise_signal, signal.SIGINT)
                    await asyncio.Future()
            else:
                if payload["name"] == "beta" and sys.argv[1] == "True":
                    asyncio.get_running_loop().call_soon(signal.raise_signal, signal.SIGINT)
                    await asyncio.Future()
                print("RESTORED " + payload["name"], flush=True)
            return {"id": payload["id"]}

        api.save_endpoint.side_effect = save
        environment = {
            "id": "env",
            "name": "prod",
            "activeBuildId": "old-build",
            "endpoints": [{"id": f"ep-{name}", "name": name} for name in names],
        }
        try:
            asyncio.run(reconcile_endpoints(
                api, app, environment, "new-build", "3.12", activate_build=True
            ))
        except KeyboardInterrupt:
            print("INTERRUPT PROPAGATED", flush=True)
        else:
            raise AssertionError("SIGINT did not propagate")
        """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(interrupt_cleanup)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "INTERRUPT PROPAGATED" in result.stdout
    assert "RESTORED gamma" in result.stdout
    if interrupt_cleanup:
        assert "RESTORED beta" not in result.stdout
        assert "RESTORED alpha" not in result.stdout
        assert "Rollback interrupted" in result.stderr
        assert "restore 'beta' (ep-beta)" in result.stderr
        assert "Restored build old-build." in result.stderr
        assert "Restored endpoint 'gamma' (ep-gamma)." in result.stderr
        assert "rp flash env get prod --app demo" in result.stderr
        assert "Remaining workers may still incur charges." in result.stderr
        assert "Changes rolled back." not in result.stderr
    else:
        assert "RESTORED beta" in result.stdout
        assert "RESTORED alpha" in result.stdout
        assert "Changes rolled back." in result.stderr
        assert "Rollback interrupted" not in result.stderr


async def test_cleanup_cancellation_preserves_completed_deletes_and_prior_errors(
    caplog,
):
    app = App("demo")
    for name in ("alpha", "beta", "gamma", "omega"):
        app.queue(name=name, cpu="cpu3c-1-2")(lambda: None)
    api = AsyncMock()
    api.save_endpoint.side_effect = [
        {"id": "ep-alpha"},
        {"id": "ep-beta"},
        {"id": "ep-gamma"},
        RuntimeError("quota exceeded"),
    ]
    api.delete_endpoint.side_effect = [
        True,
        RuntimeError("delete denied"),
        asyncio.CancelledError(),
    ]
    with pytest.raises(asyncio.CancelledError):
        await reconcile_endpoints(
            api, app, {"id": "env", "name": "prod"}, "new-build", "3.12"
        )
    assert "Couldn't deploy 'omega': quota exceeded" in caplog.text
    assert "Deleted endpoint 'gamma' (ep-gamma)." in caplog.text
    assert "beta (ep-beta): delete denied" in caplog.text
    assert (
        "Rollback interrupted while attempting to delete 'alpha' (ep-alpha)."
        in caplog.text
    )
    assert "rp flash env get prod --app demo" in caplog.text
    assert "Changes rolled back." not in caplog.text


async def test_cleanup_cancellation_during_ownership_read_reports_hint(caplog):
    app = App("demo")
    api = AsyncMock()
    api.deploy_build.side_effect = RuntimeError("activation response lost")
    api.get_environment_by_name.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await reconcile_endpoints(
            api,
            app,
            {"id": "env", "name": "prod"},
            "new-build",
            "3.12",
            activate_build=True,
        )
    assert (
        "Rollback interrupted while attempting to check deployment ownership."
        in caplog.text
    )
    assert "rp flash env get prod --app demo" in caplog.text
    assert "Changes rolled back." not in caplog.text
    api.delete_endpoint.assert_not_awaited()
