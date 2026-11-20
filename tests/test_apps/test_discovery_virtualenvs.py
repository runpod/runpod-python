import importlib
import sys
from pathlib import Path

import pytest

from runpod.apps import discovery
from runpod.apps.app import _clear_registry


@pytest.fixture(autouse=True)
def clean_discovery_state(tmp_path, monkeypatch):
    _clear_registry()
    monkeypatch.setattr(discovery, "_loaded_modules", {})
    yield
    _clear_registry()
    for name, module in list(sys.modules.items()):
        source = getattr(module, "__file__", None)
        paths = [source] if source else getattr(module, "__path__", ())
        if any(Path(path).resolve().is_relative_to(tmp_path) for path in paths):
            sys.modules.pop(name, None)


@pytest.mark.parametrize(
    ("env_name", "marked"),
    [
        ("env", True),
        (".venv", True),
        ("venv", True),
        ("custom_env", True),
        ("tools/python", True),
        (".conda", False),
        ("tools/.conda", False),
    ],
)
@pytest.mark.parametrize("preloaded", [False, True])
def test_rescans_preserve_virtualenv_dependencies(
    tmp_path, monkeypatch, env_name, marked, preloaded
):
    env = tmp_path / env_name
    site_packages = env / "lib/python3/site-packages"
    package = site_packages / "discovery_env_dependency"
    package.mkdir(parents=True)
    if marked:
        (env / "pyvenv.cfg").write_text("home = /python\n")
    (package / "dependency.py").write_text("TOKEN = object()\n")
    (site_packages / "unimported.py").write_text(
        "raise AssertionError('must not scan')\n"
    )
    monkeypatch.syspath_prepend(str(site_packages))
    if preloaded:
        dependency = importlib.import_module("discovery_env_dependency.dependency")
    target = tmp_path / "service.py"
    target.write_text(
        "from discovery_env_dependency import dependency\n"
        "from runpod import App\n"
        "app = App('service')\n"
        "@app.queue(cpu='cpu3c-1-2')\n"
        "def worker(): return dependency.TOKEN\n"
    )

    (first,) = discovery.discover_apps(tmp_path, strict=True)
    imported = sys.modules["discovery_env_dependency.dependency"]
    namespace = sys.modules["discovery_env_dependency"]
    if preloaded:
        assert imported is dependency
    assert first.resources["worker"].local() is imported.TOKEN

    (second,) = discovery.discover_apps(tmp_path, strict=True)

    assert second is not first
    assert sys.modules["discovery_env_dependency.dependency"] is imported
    assert sys.modules["discovery_env_dependency"] is namespace
    assert second.resources["worker"].local() is imported.TOKEN

    target.write_text(target.read_text() + "raise RuntimeError('broken project')\n")
    with pytest.raises(discovery.DiscoveryError, match="broken project"):
        discovery.discover_apps(tmp_path, strict=True)

    assert sys.modules["discovery_env_dependency.dependency"] is imported
    assert sys.modules["discovery_env_dependency"] is namespace
    assert namespace.dependency is imported
    assert second.resources["worker"].local() is imported.TOKEN


@pytest.mark.parametrize("package", [False, True])
def test_file_target_rescans_with_virtualenv_marker_at_project_root(tmp_path, package):
    project = tmp_path / "discovery_root_project"
    project.mkdir()
    (project / "pyvenv.cfg").write_text("home = /python\n")
    if package:
        (project / "__init__.py").write_text("")
    target = project / "service.py"
    target.write_text(
        "from runpod import App\n"
        "app = App('service')\n"
        "@app.queue(cpu='cpu3c-1-2')\n"
        "def worker(): return 1\n"
    )

    (first,) = discovery.discover_apps(target, strict=True)
    (second,) = discovery.discover_apps(target, strict=True)

    assert second is not first
    assert discovery.get_registered_apps() == [second]
    assert second.resources["worker"].local() == 1


def test_virtualenv_marker_above_project_does_not_exclude_project_modules(tmp_path):
    (tmp_path / "pyvenv.cfg").write_text("home = /python\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / "service.py").write_text(
        "from runpod import App\n"
        "app = App('service')\n"
        "@app.queue(cpu='cpu3c-1-2')\n"
        "def worker(): return 1\n"
    )

    (first,) = discovery.discover_apps(project, strict=True)
    (second,) = discovery.discover_apps(project, strict=True)

    assert first is not second
    assert second.resources["worker"].local() == 1
