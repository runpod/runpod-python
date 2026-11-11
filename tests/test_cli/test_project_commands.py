"""strict project loading for deploy and dev commands."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from runpod.apps.app import _clear_registry, get_registered_apps
from runpod.apps.entrypoint import _clear_entrypoints, get_entrypoints
from runpod.rp_cli.main import cli

APP_SOURCE = """
import runpod
app = runpod.App("demo")
@app.queue(name="work", cpu="cpu3c-1-2")
def work():
    return 1
"""

HOOK_SOURCE = """
@runpod.local_entrypoint
def main():
    pass
"""


@pytest.fixture(autouse=True)
def clean_project_state(tmp_path, monkeypatch):
    _clear_registry()
    _clear_entrypoints()
    monkeypatch.delenv("RUNPOD_DEV_SESSION", raising=False)
    monkeypatch.setattr("runpod.rp_cli.update.start_background_check", lambda: None)
    yield
    _clear_registry()
    _clear_entrypoints()
    for name, module in list(sys.modules.items()):
        source = getattr(module, "__file__", None)
        if source and Path(source).resolve().is_relative_to(tmp_path):
            sys.modules.pop(name, None)


@pytest.fixture
def session_factory():
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.refresh = AsyncMock()
    session._endpoints = {"active": "ep-existing"}

    def create(apps, events=None):
        session.apps = apps
        return session

    with patch("runpod.apps.dev.DevSession", side_effect=create) as factory:
        yield session, factory


@pytest.mark.parametrize("target", [None, ".", "main.py"])
def test_dev_without_hook_provisions_and_cleans_up(
    tmp_path, monkeypatch, session_factory, target
):
    (tmp_path / "main.py").write_text(APP_SOURCE)
    monkeypatch.chdir(tmp_path)
    session, _ = session_factory
    args = ["flash", "dev", "--once"]
    if target is not None:
        args.append(target)
    with patch("runpod.apps.entrypoint.run_entrypoint") as run:
        result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    session.start.assert_awaited_once()
    session.stop.assert_awaited_once()
    run.assert_not_called()


def test_dev_without_hook_watches_and_refreshes(tmp_path, session_factory):
    (tmp_path / "main.py").write_text(APP_SOURCE)
    session, _ = session_factory
    with (
        patch("runpod.apps.watch.FileWatcher") as watcher,
        patch("runpod.apps.entrypoint.run_entrypoint") as run,
    ):
        watcher.return_value.changed.side_effect = [True, KeyboardInterrupt]
        result = CliRunner().invoke(cli, ["flash", "dev", str(tmp_path)])
    assert result.exit_code == 0, result.output
    watcher.assert_called_once_with([tmp_path.resolve()])
    session.refresh.assert_awaited_once()
    session.stop.assert_awaited_once()
    run.assert_not_called()


def test_multiple_hooks_require_selection_before_provisioning(
    tmp_path, session_factory
):
    (tmp_path / "first.py").write_text(APP_SOURCE + HOOK_SOURCE)
    (tmp_path / "second.py").write_text("import runpod\n" + HOOK_SOURCE)
    _, factory = session_factory
    result = CliRunner().invoke(cli, ["flash", "dev", str(tmp_path), "--once"])
    assert result.exit_code == 1
    assert "first:main" in result.output
    assert "second:main" in result.output
    assert "--entrypoint" in result.output
    factory.assert_not_called()


def test_explicit_hook_selection(tmp_path, session_factory):
    (tmp_path / "first.py").write_text(APP_SOURCE + HOOK_SOURCE)
    (tmp_path / "second.py").write_text("import runpod\n" + HOOK_SOURCE)
    with patch("runpod.apps.entrypoint.run_entrypoint") as run:
        result = CliRunner().invoke(
            cli,
            ["flash", "dev", str(tmp_path), "--once", "--entrypoint", "second:main"],
        )
    assert result.exit_code == 0, result.output
    run.assert_called_once()
    assert Path(run.call_args.args[0].__code__.co_filename).name == "second.py"


def test_unknown_hook_does_not_provision(tmp_path, session_factory):
    (tmp_path / "main.py").write_text(APP_SOURCE + HOOK_SOURCE)
    _, factory = session_factory
    result = CliRunner().invoke(
        cli,
        ["flash", "dev", str(tmp_path), "--once", "--entrypoint", "missing:main"],
    )
    assert result.exit_code == 1
    assert "not found" in result.output
    assert "main:main" in result.output
    factory.assert_not_called()


def test_package_file_watches_outer_package(tmp_path, session_factory):
    package = tmp_path / "dev_watch_package"
    nested = package / "nested"
    nested.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (nested / "__init__.py").write_text("")
    target = nested / "main.py"
    target.write_text(APP_SOURCE + HOOK_SOURCE)
    with (
        patch("runpod.apps.watch.FileWatcher") as watcher,
        patch("runpod.apps.entrypoint.run_entrypoint"),
    ):
        result = CliRunner().invoke(
            cli,
            [
                "flash",
                "dev",
                str(target),
                "--once",
                "--entrypoint",
                "dev_watch_package.nested.main:main",
            ],
        )
    assert result.exit_code == 0, result.output
    watcher.assert_called_once_with([package.resolve()])


@pytest.mark.parametrize("failure", ["import", "validation", "selection"])
def test_failed_reload_preserves_session_and_waits_for_recovery(
    tmp_path, session_factory, failure
):
    target = tmp_path / "main.py"
    source = APP_SOURCE + HOOK_SOURCE
    target.write_text(source)
    session, _ = session_factory
    checks = 0
    previous_hooks = None
    previous_apps = None

    def changed():
        nonlocal checks, previous_hooks, previous_apps
        checks += 1
        if checks == 1:
            previous_hooks = get_entrypoints()
            previous_apps = get_registered_apps()
            if failure == "import":
                target.write_text(source + "\nraise RuntimeError('broken reload')\n")
            elif failure == "validation":
                target.write_text(source + "\nwork.spec.gpu_count = 0\n")
            else:
                target.write_text(
                    source + "\n@runpod.local_entrypoint\ndef other(): pass\n"
                )
            return True
        if checks == 2:
            return False
        if checks == 3:
            session.refresh.assert_not_awaited()
            session.stop.assert_not_awaited()
            assert session._endpoints == {"active": "ep-existing"}
            assert get_entrypoints() == previous_hooks
            assert session.apps == previous_apps
            run.assert_called_once()
            target.write_text(source)
            return True
        raise KeyboardInterrupt

    with (
        patch("queue.Queue") as stdin_queue,
        patch("runpod.apps.watch.FileWatcher.changed", side_effect=changed),
        patch("runpod.apps.entrypoint.run_entrypoint") as run,
    ):
        stdin_queue.return_value.get_nowait.return_value = "\n"
        result = CliRunner().invoke(cli, ["flash", "dev", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "reload failed" in result.output
    assert "keeping current endpoints" in " ".join(result.output.split())
    assert checks == 4
    assert run.call_count == 2
    session.refresh.assert_awaited_once()
    session.stop.assert_awaited_once()


@pytest.mark.parametrize("build_only", [False, True])
@pytest.mark.parametrize("failure", ["import", "empty", "invalid", "duplicate"])
def test_deploy_preflights_every_app_before_build_or_mutation(
    tmp_path, build_only, failure
):
    (tmp_path / "first.py").write_text(APP_SOURCE)
    second = APP_SOURCE.replace('"demo"', '"second"')
    if failure == "import":
        second += "\nraise RuntimeError('broken candidate')\n"
    elif failure == "empty":
        second = 'import runpod\napp = runpod.App("second")\n'
    elif failure == "invalid":
        second += "\nwork.spec.gpu_count = 0\n"
    else:
        second = APP_SOURCE
    (tmp_path / "second.py").write_text(second)
    with (
        patch("runpod.apps.deploy.build_artifact") as build,
        patch("runpod.apps.deploy.deploy_app", new_callable=AsyncMock) as deploy,
    ):
        args = ["flash", "deploy", str(tmp_path), "--python-version", "3.11"]
        if build_only:
            args.append("--build-only")
        result = CliRunner().invoke(cli, args)
    assert result.exit_code == 1, result.output
    assert "Error:" in result.output
    build.assert_not_called()
    deploy.assert_not_awaited()


@pytest.mark.parametrize("failure", ["import", "empty", "invalid", "duplicate"])
def test_dev_preflights_every_app_before_provisioning(
    tmp_path, session_factory, failure
):
    (tmp_path / "first.py").write_text(APP_SOURCE)
    second = APP_SOURCE.replace('"demo"', '"second"')
    if failure == "import":
        second += "\nraise RuntimeError('broken candidate')\n"
    elif failure == "empty":
        second = 'import runpod\napp = runpod.App("second")\n'
    elif failure == "invalid":
        second += "\nwork.spec.gpu_count = 0\n"
    else:
        second = APP_SOURCE
    (tmp_path / "second.py").write_text(second)
    _, factory = session_factory
    result = CliRunner().invoke(cli, ["flash", "dev", str(tmp_path), "--once"])
    assert result.exit_code == 1, result.output
    assert "Error:" in result.output
    factory.assert_not_called()


def test_directory_dev_and_deploy_discover_identical_resources(
    tmp_path, session_factory
):
    from runpod.apps.deploy import DeployResult

    (tmp_path / "shared.py").write_text('import runpod\napp = runpod.App("shared")\n')
    for name in ("first", "second"):
        (tmp_path / f"{name}.py").write_text(
            "from shared import app\n"
            f'@app.queue(name="{name}", cpu="cpu3c-1-2")\n'
            f"def {name}(): return 1\n"
        )
    session, _ = session_factory
    dev = CliRunner().invoke(cli, ["flash", "dev", str(tmp_path), "--once"])
    assert dev.exit_code == 0, dev.output
    dev_resources = [(app.name, sorted(app.resources)) for app in session.apps]
    deployed = DeployResult(
        app_name="shared",
        build_id="b1",
        environment_id="env1",
        resources=["first", "second"],
        endpoints={},
    )
    with patch(
        "runpod.apps.deploy.deploy_app", AsyncMock(return_value=deployed)
    ) as deploy:
        result = CliRunner().invoke(
            cli, ["flash", "deploy", str(tmp_path), "--python-version", "3.11"]
        )
    assert result.exit_code == 0, result.output
    app = deploy.call_args.args[0]
    assert dev_resources == [(app.name, sorted(app.resources))]
    assert dev_resources == [("shared", ["first", "second"])]


def test_build_package_file_uses_outer_source_root(tmp_path):
    package = tmp_path / "deploy_root_package"
    nested = package / "nested"
    nested.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (nested / "__init__.py").write_text("")
    target = nested / "main.py"
    target.write_text(APP_SOURCE)
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_bytes(b"artifact")
    with patch("runpod.apps.deploy.build_artifact", return_value=artifact) as build:
        result = CliRunner().invoke(
            cli,
            [
                "flash",
                "deploy",
                str(target),
                "--build-only",
                "--python-version",
                "3.11",
            ],
        )
    assert result.exit_code == 0, result.output
    assert build.call_args.args[1] == package.resolve()
