import importlib
import json
import subprocess
import sys
import tarfile
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest

from runpod.apps import App, get_registered_apps
from runpod.apps.app import _clear_registry
from runpod.apps.deploy import (
    build_artifact,
    build_manifest,
    deploy_app,
    package_project,
)
from runpod.apps.discovery import (
    DiscoveryError,
    discover_apps,
    resolve_project,
)
from runpod.apps.errors import InvalidResourceError


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


def write_project(root, definition="app", package=False):
    root.mkdir(parents=True, exist_ok=True)
    if package:
        (root / "__init__.py").write_text("")
    (root / f"{definition}.py").write_text(
        "from runpod import App\napp = App('service')\n"
    )
    for module, resource in (("a_workers", "alpha"), ("z_workers", "bravo")):
        (root / f"{module}.py").write_text(
            f"from {'.' if package else ''}{definition} import app\n"
            f"@app.queue(name='{resource}', cpu='cpu3c-1-2')\n"
            f"def {resource}(value):\n    return value * 2\n"
        )


@pytest.mark.parametrize("definition", ["aaa_app", "common", "zzz_app"])
@pytest.mark.parametrize("package", [False, True])
def test_shared_app_is_independent_of_file_order(tmp_path, definition, package):
    root = tmp_path / "project"
    write_project(root, definition, package)

    for _ in range(2):
        (app,) = discover_apps(root, strict=True)
        prefix = "project." if package else ""
        defining = importlib.import_module(f"{prefix}{definition}")
        assert app is defining.app
        assert set(app.resources) == {"alpha", "bravo"}
        assert get_registered_apps() == [app]
        assert app._source_file == (root / f"{definition}.py").resolve()
        for module, resource in (("a_workers", "alpha"), ("z_workers", "bravo")):
            imported = importlib.import_module(f"{prefix}{module}")
            assert imported.app is app
            assert app.resources[resource] is getattr(imported, resource)
            assert app.resources[resource].local(3) == 6


def test_package_absolute_relative_and_dynamic_imports_share_identity(tmp_path):
    root = tmp_path / "service"
    write_project(root, package=True)
    (root / "__init__.py").write_text("from .app import app\n")
    (root / "z_workers.py").write_text(
        "import importlib\n"
        "from service import app\n"
        "assert app is importlib.import_module('service.app').app\n"
        "@app.queue(name='bravo', cpu='cpu3c-1-2')\n"
        "def bravo(value): return value\n"
    )

    (app,) = discover_apps(root, strict=True)

    assert set(app.resources) == {"alpha", "bravo"}


@pytest.mark.parametrize("package", [False, True])
def test_single_file_target_uses_its_import_graph(tmp_path, package):
    root = tmp_path / "project"
    write_project(root, package=package)

    (app,) = discover_apps(root / "a_workers.py", strict=True)

    assert set(app.resources) == {"alpha"}


def test_nested_namespace_modules_have_distinct_names(tmp_path):
    for name in ("first", "second"):
        root = tmp_path / name
        root.mkdir()
        (root / "worker.py").write_text(
            f"from runpod import App\napp = App('{name}')\n"
            "@app.queue(cpu='cpu3c-1-2')\n"
            "def generate(): return 1\n"
        )

    apps = discover_apps(tmp_path, strict=True)

    assert {app.name for app in apps} == {"first", "second"}
    assert sys.modules["first.worker"].app is not sys.modules["second.worker"].app


def test_discovery_and_watch_use_project_relative_exclusions(tmp_path):
    from runpod.apps.watch import FileWatcher

    root = tmp_path / "build" / "service"
    write_project(root)
    (app,) = discover_apps(root, strict=True)
    watcher = FileWatcher([root])
    (root / "helper.py").write_text("value = 1\n")

    assert set(app.resources) == {"alpha", "bravo"}
    assert watcher.changed()


def test_distinct_apps_with_same_name_fail_with_both_sources(tmp_path):
    for filename in ("first.py", "second.py"):
        (tmp_path / filename).write_text(
            "from runpod import App\napp = App('shared')\n"
        )

    with pytest.raises(DiscoveryError, match="distinct Apps named 'shared'") as exc:
        discover_apps(tmp_path, strict=True)

    assert "first.py" in str(exc.value)
    assert "second.py" in str(exc.value)
    assert get_registered_apps() == []


def test_unrelated_cached_module_collision_is_not_overwritten(tmp_path, monkeypatch):
    unrelated = ModuleType("workers")
    unrelated.__file__ = "/unrelated/workers.py"
    monkeypatch.setitem(sys.modules, "workers", unrelated)
    (tmp_path / "workers.py").write_text("raise AssertionError('must not execute')\n")

    with pytest.raises(DiscoveryError, match="resolves to /unrelated/workers.py"):
        discover_apps(tmp_path, strict=True)

    assert sys.modules["workers"] is unrelated


def test_strict_scan_failure_restores_last_successful_project(tmp_path):
    root = tmp_path / "project"
    write_project(root, package=True)
    (root / "__init__.py").write_text("from .app import app\n")
    (app,) = discover_apps(root, strict=True)
    package = sys.modules["project"]
    original_module = sys.modules["project.a_workers"]
    original_path = sys.path[:]
    (root / "z_workers.py").write_text("raise RuntimeError('broken import')\n")

    with pytest.raises(DiscoveryError, match="broken import"):
        discover_apps(root, strict=True)

    assert get_registered_apps() == [app]
    assert set(app.resources) == {"alpha", "bravo"}
    assert sys.modules["project.a_workers"] is original_module
    assert package.a_workers is original_module
    assert package.app is app
    assert sys.path == original_path


def test_empty_strict_scan_restores_previous_project(tmp_path):
    root = tmp_path / "project"
    write_project(root)
    (app,) = discover_apps(root, strict=True)
    (root / "a_workers.py").unlink()
    (root / "z_workers.py").unlink()

    with pytest.raises(InvalidResourceError, match="has no resources"):
        discover_apps(root, strict=True)

    assert get_registered_apps() == [app]
    assert sys.modules["app"].app is app


def test_strict_scan_without_apps_restores_previous_project(tmp_path):
    write_project(tmp_path)
    (app,) = discover_apps(tmp_path, strict=True)
    for path in tmp_path.glob("*.py"):
        path.write_text("")

    with pytest.raises(DiscoveryError, match="no runpod.App found"):
        discover_apps(tmp_path, strict=True)

    assert get_registered_apps() == [app]
    assert sys.modules["app"].app is app


@pytest.mark.parametrize("target_kind", ["flat", "package", "nested_file"])
def test_artifact_resources_import_in_a_fresh_process(tmp_path, target_kind):
    root = tmp_path / "source" / "service"
    write_project(root, package=target_kind != "flat")
    target = root
    if target_kind == "nested_file":
        nested = root / "jobs"
        nested.mkdir()
        (nested / "__init__.py").write_text("")
        target = nested / "worker.py"
        target.write_text(
            "from ..app import app\n"
            "from ..a_workers import alpha\n"
            "@app.queue(name='nested', cpu='cpu3c-1-2')\n"
            "def nested(value): return alpha.local(value) + 1\n"
        )
    project = resolve_project(target)
    (app,) = discover_apps(target, strict=True)
    manifest = build_manifest(app, project.source_root)
    artifact = package_project(
        project.source_root, manifest, output=tmp_path / "artifact.tar.gz"
    )
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    with tarfile.open(artifact) as archive:
        archive.extractall(extracted, filter="data")
    script = """
import importlib
import json
import sys
from pathlib import Path
from runpod.apps.handles import FunctionHandle

sys.path.insert(0, str(Path.cwd()))
manifest = json.loads(Path('runpod_manifest.json').read_text())
results = {}
for resource in manifest['resources']:
    module = importlib.import_module(resource['module'])
    handles = [handle for handle in vars(module).values()
               if isinstance(handle, FunctionHandle)
               and handle.spec.name == resource['name']]
    assert len(handles) == 1, resource
    assert handles[0]._fn.__module__ == resource['module']
    results[resource['name']] = handles[0].local(3)
print(json.dumps(results))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=extracted,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    expected = (
        {"alpha": 6, "nested": 7}
        if target_kind == "nested_file"
        else {"alpha": 6, "bravo": 6}
    )
    assert json.loads(result.stdout) == expected


def test_empty_artifact_fails_before_building(tmp_path):
    app = App("empty")
    with patch("runpod.apps.deploy.build_environment") as build:
        with pytest.raises(InvalidResourceError, match="has no resources"):
            build_artifact(app, tmp_path)
    build.assert_not_called()


async def test_empty_deploy_fails_before_remote_mutations(tmp_path):
    api = AsyncMock()
    with pytest.raises(InvalidResourceError, match="has no resources"):
        await deploy_app(App("empty"), tmp_path, api=api)
    assert not api.mock_calls
