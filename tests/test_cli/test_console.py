"""rendering tests for the rp cli console module."""

import io

import pytest
from rich.console import Console

from runpod.rp_cli import console as ui


@pytest.fixture(autouse=True)
def capture_console(monkeypatch):
    """swap the module console for one writing to a buffer."""
    buffer = io.StringIO()
    test_console = Console(
        file=buffer,
        highlight=False,
        theme=ui.theme,
        force_terminal=False,
        width=120,
    )
    monkeypatch.setattr(ui, "console", test_console)
    yield buffer


def _out(buffer):
    return buffer.getvalue()


class TestGenericLines:
    def test_info(self, capture_console):
        ui.info("hello")
        assert "hello" in _out(capture_console)

    def test_success(self, capture_console):
        ui.success("done")
        assert "✓ done" in _out(capture_console)

    def test_error(self, capture_console):
        ui.error("bad")
        assert "✗ bad" in _out(capture_console)

    def test_warn(self, capture_console):
        ui.warn("careful")
        assert "! careful" in _out(capture_console)


class TestHelpers:
    def test_endpoint_url(self):
        assert ui.endpoint_url("ep1").endswith("/ep1?tab=overview")

    def test_endpoint_link_markup(self):
        assert "ep1 ↗" in ui.endpoint_link("ep1")

    def test_name_width_padding(self):
        ui.set_name_width(["a", "longer"])
        assert ui._padded("a") == "a     "
        ui.set_name_width([])
        assert ui._padded("a") == "a"

    def test_kind_badge_known_and_unknown(self):
        assert "queue" in ui.kind_badge("queue")
        assert "other" in ui.kind_badge("other")

    def test_fmt_elapsed(self):
        assert ui._fmt_elapsed(0.5) == "500ms"
        assert ui._fmt_elapsed(3.21) == "3.2s"
        assert ui._fmt_elapsed(150) == "2m30s"

    def test_bar_bounds(self):
        assert "━" in ui._bar(0.5)
        ui._bar(-1)
        ui._bar(2)

    def test_sand_spinner_registered(self):
        from rich.spinner import SPINNERS

        assert ui.RUNPOD_SAND_SPINNER in SPINNERS
        assert (
            SPINNERS[ui.RUNPOD_SAND_SPINNER]["frames"]
            == ui.RUNPOD_SAND_SPINNER_FRAMES
        )


class TestBanners:
    def test_dev_banner(self, capture_console):
        ui.dev_banner(["demo"], "main.py")
        assert "demo in main.py" in _out(capture_console)

    def test_deploy_plan(self, capture_console):
        ui.deploy_plan([("demo", "main.py", 2), ("other", "", 1)])
        out = _out(capture_console)
        assert "2 apps" in out
        assert "main.py, 2 resources" in out
        assert "1 resource" in out

    def test_deploy_banner(self, capture_console):
        ui.deploy_banner("demo", "prod", [("chat", "queue")], source="main.py")
        out = _out(capture_console)
        assert "demo" in out
        assert "prod" in out
        assert "chat" in out

    def test_deploy_banner_no_resources(self, capture_console):
        ui.deploy_banner("demo", "prod", [])
        assert "(no resources)" in _out(capture_console)

    def test_resources_table(self, capture_console):
        ui.resources_table(
            [
                ("chat", "queue", "4090", "ep1"),
                ("train", "task", "H100", "per-call"),
            ]
        )
        out = _out(capture_console)
        assert "chat" in out
        assert "per-call" in out

    def test_resources_table_empty(self, capture_console):
        ui.resources_table([])
        assert _out(capture_console) == ""

    def test_reload_banner(self, capture_console):
        ui.reload_banner("main.py")
        assert "reloading" in _out(capture_console)

    def test_entrypoint_lines(self, capture_console):
        ui.entrypoint_header("main")
        ui.entrypoint_success(1.2)
        ui.entrypoint_failure(3.4, "boom")
        out = _out(capture_console)
        assert "main()" in out
        assert "done" in out
        assert "boom" in out


class TestDeployEvents:
    def test_phase_lifecycle(self, capture_console):
        events = ui.DeployEvents()
        events.phase("vendor")
        events.vendor_progress(3, "torch")
        events.phase("upload")
        events.upload_progress(512 * 1024, 1024 * 1024)
        events.phase("endpoints")
        events.endpoint_ready("chat", "ep1")
        events.close()
        assert events.endpoints == {"chat": "ep1"}

    def test_progress_updates_ignored_outside_phase(self):
        events = ui.DeployEvents()
        events.vendor_progress(1, "torch")
        events.upload_progress(1, 2)
        events.close()


class TestDevEvents:
    def test_request_lifecycle(self, capture_console):
        events = ui.DevEvents()
        events.dispatch("chat", "queued")
        events.worker_ready("chat", "worker123456789")
        events.worker_log("chat", "processing [1/3]")
        events.worker_status("chat", {"initializing": 1})
        events.task_status("chat", "pod starting")
        events.request_completed("chat", 2.5)
        events.request_failed("chat", 1.0)
        out = _out(capture_console)
        assert "→ chat()" in out
        assert "worker worker123456 ready" in out
        assert "processing [1/3]" in out  # markup escaped, shown verbatim
        assert "waiting: 1 initializing" in out
        assert "pod starting" in out
        assert "✓ chat()" in out
        assert "✗ chat()" in out

    def test_provision_rows_without_progress(self, capture_console):
        events = ui.DevEvents()
        events.provisioning("chat", "queue", "4090")
        events.adopted("chat", "ep1")
        events.ready("chat", "ep1")
        events.deleted("chat")
        out = _out(capture_console)
        assert "provisioning queue on 4090" in out
        assert "− chat" in out

    def test_session_progress_lifecycle(self, capture_console):
        events = ui.DevEvents()
        events.session_starting()
        events.provisioning("chat", "queue", "4090")
        events.ready("chat", "ep1")
        events.session_started()
        assert events._progress is None

    def test_refresh_diff(self, capture_console):
        events = ui.DevEvents()
        events.resource_added("new", "queue", "4090")
        events.resource_changed("chat", ["gpu"])
        events.resource_changed("chat", [])
        events.resource_removed("old")
        events.volume_created("data", 10, "US-KS-2")
        out = _out(capture_console)
        assert "+ new" in out
        assert "~ chat" in out
        assert "− old" in out
        assert "volume data" in out


class TestCleanupEvents:
    def test_zero_total_is_silent(self, capture_console):
        events = ui.CleanupEvents()
        events.cleanup_started(0)
        events.deleting("x")
        events.deleted("x")
        events.close()

    def test_delete_progress(self, capture_console):
        events = ui.CleanupEvents()
        events.cleanup_started(2)
        events.deleting("ep1")
        events.deleted("ep1")
        events.delete_failed("ep2")
        events.close()
        assert "could not delete ep2" in _out(capture_console)


class TestTimer:
    def test_elapsed(self):
        with ui.Timer() as t:
            assert t.so_far >= 0
        assert t.elapsed >= 0
