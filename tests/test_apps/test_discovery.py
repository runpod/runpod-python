import importlib
import os
import sys
from pathlib import Path

import pytest

from runpod.apps import App, get_registered_apps
from runpod.apps.app import _clear_registry
from runpod.apps.discovery import DiscoveryError, discover_apps
from runpod.apps.discovery_state import DISCOVERY_ENV


@pytest.fixture(autouse=True)
def clean_discovery_state(tmp_path):
    _clear_registry()
    yield
    _clear_registry()
    for name, module in list(sys.modules.items()):
        source = getattr(module, "__file__", None)
        if source and Path(source).resolve().is_relative_to(tmp_path):
            sys.modules.pop(name, None)


def test_failed_module_does_not_return_partial_app(tmp_path):
    existing = App("existing")
    (tmp_path / "broken.py").write_text(
        "from runpod import App\n"
        "app = App('partial')\n"
        "raise RuntimeError('incomplete initialization')\n"
    )
    (tmp_path / "healthy.py").write_text(
        "from runpod import App\napp = App('healthy')\n"
    )

    apps = discover_apps(tmp_path)

    assert [app.name for app in apps] == ["healthy"]
    assert get_registered_apps() == [existing, apps[0]]


def test_failed_package_import_can_be_retried_without_orphaned_app(tmp_path):
    package = tmp_path / "discovery_retry_package"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "helper.py").write_text(
        "from runpod import App\napp = App('recovered')\n"
    )
    target = package / "main.py"
    target.write_text("from .helper import app\nraise RuntimeError('broken')\n")
    existing = App("existing")

    with pytest.raises(DiscoveryError):
        discover_apps(target)
    assert get_registered_apps() == [existing]

    target.write_text("from .helper import app\n")
    apps = discover_apps(target)

    assert [app.name for app in apps] == ["recovered"]
    assert importlib.import_module("discovery_retry_package.helper").app is apps[0]
    assert get_registered_apps() == [existing, apps[0]]


@pytest.mark.parametrize("single_file", [False, True])
def test_package_relative_imports_preserve_local_handle_identity(tmp_path, single_file):
    package = tmp_path / "discovery_identity_package"
    package.mkdir()
    (package / "__init__.py").write_text("from .main import app, double\n")
    (package / "helpers.py").write_text("FACTOR = 2\n")
    target = package / "main.py"
    target.write_text(
        "from runpod import App\n"
        "from .helpers import FACTOR\n"
        "app = App('package')\n"
        "@app.queue(cpu='cpu3c-1-2')\n"
        "def double(value):\n"
        "    return value * FACTOR\n"
    )
    existing = App("outside-discovery")
    helper = package / "helpers.py"
    original_mtime = helper.stat().st_mtime_ns
    for factor in (2, 3):
        helper.write_text(f"FACTOR = {factor}\n")
        os.utime(helper, ns=(original_mtime, original_mtime))

        apps = discover_apps(target if single_file else tmp_path)
        module = importlib.import_module("discovery_identity_package.main")
        parent = importlib.import_module("discovery_identity_package")

        assert apps == [module.app]
        assert get_registered_apps() == [existing, *apps]
        assert parent.app is module.app
        assert apps[0].resources["double"] is module.double
        assert module.double.local(3) == 3 * factor


def test_discovery_restores_existing_invocation_guard(tmp_path, monkeypatch):
    target = tmp_path / "main.py"
    target.write_text("raise RuntimeError('broken')\n")
    monkeypatch.setenv(DISCOVERY_ENV, "outer-scan")

    with pytest.raises(DiscoveryError):
        discover_apps(target)

    assert os.environ[DISCOVERY_ENV] == "outer-scan"


def test_interrupt_aborts_discovery_and_rolls_back_partial_import(
    tmp_path, monkeypatch
):
    monkeypatch.delenv(DISCOVERY_ENV, raising=False)
    existing = App("existing")
    (tmp_path / "a_interrupted.py").write_text(
        "from runpod import App\n" "app = App('partial')\n" "raise KeyboardInterrupt\n"
    )
    (tmp_path / "b_later.py").write_text(
        "from pathlib import Path\n" "Path(__file__).with_suffix('.started').touch()\n"
    )

    with pytest.raises(KeyboardInterrupt):
        discover_apps(tmp_path)

    assert get_registered_apps() == [existing]
    assert not (tmp_path / "b_later.started").exists()
    assert DISCOVERY_ENV not in os.environ


def test_directory_scan_skips_non_application_files(tmp_path):
    (tmp_path / "service.py").write_text(
        "from runpod import App\n"
        "app = App('service')\n"
        "@app.queue(cpu='cpu3c-1-2')\n"
        "def worker(): return 1\n"
    )
    for name in (
        "tests/test_app.py",
        "setup.py",
        ".hidden/script.py",
        "custom_env/script.py",
        "git_ignored.py",
        "runpod_ignored.py",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("from runpod import App\nApp('service')\nraise SystemExit\n")
    (tmp_path / "custom_env/pyvenv.cfg").write_text("home = /python\n")
    (tmp_path / ".gitignore").write_text("git_ignored.py\n")
    (tmp_path / ".runpodignore").write_text("runpod_ignored.py\n")

    (app,) = discover_apps(tmp_path, strict=True)

    assert set(app.resources) == {"worker"}


def test_import_failure_suggests_runpodignore(tmp_path):
    (tmp_path / "broken.py").write_text("raise RuntimeError('broken')\n")

    with pytest.raises(DiscoveryError, match=r"add it to \.runpodignore"):
        discover_apps(tmp_path, strict=True)


def test_stdlib_collision_suggests_renaming(tmp_path):
    original = importlib.import_module("queue")
    (tmp_path / "queue.py").write_text("raise AssertionError('must not import')\n")

    with pytest.raises(DiscoveryError) as exc:
        discover_apps(tmp_path, strict=True)

    assert "Rename the project module" in str(exc.value)
    assert "shadowing an installed or standard-library module" in str(exc.value)
    assert sys.modules["queue"] is original


@pytest.mark.parametrize(
    "name, hint",
    [("service", "package-relative imports"), ("my-svc", "valid python identifier")],
)
def test_package_layout_error_names_initializer(tmp_path, name, hint):
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "local_app.py").write_text("from runpod import App\napp = App('svc')\n")
    (package / "worker.py").write_text("from local_app import app\n")

    with pytest.raises(DiscoveryError) as exc:
        discover_apps(package, strict=True)

    assert str(package / "__init__.py") in str(exc.value)
    assert hint in str(exc.value)


def test_project_path_additions_support_lazy_imports(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", sys.path[:])
    project = tmp_path / "project"
    project.mkdir()
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "vendor_helper.py").write_text("VALUE = 42\n")
    (project / "service.py").write_text(
        "import sys\n"
        f"sys.path.append({str(vendor)!r})\n"
        "from runpod import App\n"
        "app = App('service')\n"
        "@app.queue(cpu='cpu3c-1-2')\n"
        "def worker():\n"
        "    from vendor_helper import VALUE\n"
        "    return VALUE\n"
    )
    sys.path.append(str(project))
    previous_path = sys.path[:]

    (app,) = discover_apps(project, strict=True)

    assert sys.path == [*previous_path, str(vendor)]
    assert app.resources["worker"].local() == 42
