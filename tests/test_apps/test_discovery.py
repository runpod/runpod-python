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

    apps = discover_apps(target if single_file else tmp_path)
    module = importlib.import_module("discovery_identity_package.main")

    assert apps == [module.app]
    assert get_registered_apps() == apps
    assert apps[0].resources["double"] is module.double
    assert module.double.local(3) == 6


def test_discovery_restores_existing_invocation_guard(tmp_path, monkeypatch):
    target = tmp_path / "main.py"
    target.write_text("raise RuntimeError('broken')\n")
    monkeypatch.setenv(DISCOVERY_ENV, "outer-scan")

    with pytest.raises(DiscoveryError):
        discover_apps(target)

    assert os.environ[DISCOVERY_ENV] == "outer-scan"


def test_interrupt_aborts_discovery_and_rolls_back_partial_import(tmp_path, monkeypatch):
    monkeypatch.delenv(DISCOVERY_ENV, raising=False)
    existing = App("existing")
    (tmp_path / "a_interrupted.py").write_text(
        "from runpod import App\n"
        "app = App('partial')\n"
        "raise KeyboardInterrupt\n"
    )
    (tmp_path / "b_later.py").write_text(
        "from pathlib import Path\n"
        "Path(__file__).with_suffix('.started').touch()\n"
    )

    with pytest.raises(KeyboardInterrupt):
        discover_apps(tmp_path)

    assert get_registered_apps() == [existing]
    assert not (tmp_path / "b_later.started").exists()
    assert DISCOVERY_ENV not in os.environ
