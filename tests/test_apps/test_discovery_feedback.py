import importlib
import sys
import tarfile
from pathlib import Path

import pytest

from runpod.apps.app import _clear_registry
from runpod.apps.deploy import package_project
from runpod.apps.discovery import DiscoveryError, discover_apps


@pytest.fixture(autouse=True)
def clean_discovery_state(tmp_path):
    previous_path = sys.path[:]
    _clear_registry()
    yield
    sys.path[:] = previous_path
    _clear_registry()
    for name, module in list(sys.modules.items()):
        source = getattr(module, "__file__", None)
        if source and Path(source).resolve().is_relative_to(tmp_path):
            sys.modules.pop(name, None)


def write_app(root):
    root.mkdir(parents=True, exist_ok=True)
    target = root / "feedback_app.py"
    target.write_text(
        "from runpod import App\n"
        "app = App('svc')\n"
        "@app.queue(cpu='cpu3c-1-2')\n"
        "def worker(value): return value\n"
    )
    return target


def test_scan_uses_packaging_ignores_without_executing_setup(tmp_path):
    write_app(tmp_path)
    excluded = [
        "tests/test_app.py",
        "test/fixture.py",
        "test_fixture.py",
        "fixture_test.py",
        "env/lib/python3.12/site.py",
        "git_ignored.py",
        "runpod_ignored.py",
        ".aws/helper.py",
    ]
    for name in excluded:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("from runpod import App\napp = App('svc')\n")
    (tmp_path / "setup.py").write_text("raise SystemExit('setup executed')\n")
    (tmp_path / ".gitignore").write_text("git_ignored.py\n")
    (tmp_path / ".runpodignore").write_text("runpod_ignored.py\n!.aws/\n")

    (app,) = discover_apps(tmp_path, strict=True)

    assert set(app.resources) == {"worker"}
    with tarfile.open(package_project(tmp_path, {})) as archive:
        names = set(archive.getnames())
    assert not names.intersection(excluded)
    assert "setup.py" in names
    assert "feedback_app.py" in names


@pytest.mark.parametrize("directory", [".github/scripts", ".hidden", "custom_env"])
def test_scan_prunes_hidden_directories_and_virtualenvs(tmp_path, directory):
    write_app(tmp_path)
    root = tmp_path / directory
    root.mkdir(parents=True)
    (root / "broken.py").write_text("raise AssertionError('must not import')\n")
    if directory == "custom_env":
        (root / "pyvenv.cfg").write_text("home = /python\n")

    (app,) = discover_apps(tmp_path, strict=True)

    assert set(app.resources) == {"worker"}


def test_scan_and_packaging_share_nested_ignore_precedence(tmp_path):
    nested = tmp_path / "nested"
    target = write_app(nested)
    (tmp_path / ".gitignore").write_text("*.py\n")
    (nested / ".gitignore").write_text("!*.py\n")
    (tmp_path / ".runpodignore").write_text("nested/drop.py\n")
    (nested / "drop.py").write_text("raise AssertionError('must not import')\n")
    (tmp_path / "other.py").write_text("raise AssertionError('must not import')\n")

    (app,) = discover_apps(tmp_path, strict=True)

    assert app._source_file == target
    with tarfile.open(package_project(tmp_path, {})) as archive:
        names = set(archive.getnames())
    assert "nested/feedback_app.py" in names
    assert "nested/drop.py" not in names
    assert "other.py" not in names


def test_package_subdirectory_scan_honors_root_ignores(tmp_path):
    package = tmp_path / "feedback_package"
    nested = package / "jobs"
    write_app(nested)
    (package / "__init__.py").write_text("")
    (nested / "__init__.py").write_text("")
    (package / ".runpodignore").write_text("jobs/broken.py\n")
    (nested / "broken.py").write_text("raise AssertionError('must not import')\n")

    (app,) = discover_apps(nested, strict=True)

    assert set(app.resources) == {"worker"}


def test_scan_does_not_follow_source_symlinks(tmp_path):
    project = tmp_path / "project"
    write_app(project)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "broken.py").write_text("raise AssertionError('must not import')\n")
    (project / "linked.py").symlink_to(outside / "broken.py")
    (project / "linked_dir").symlink_to(outside, target_is_directory=True)

    (app,) = discover_apps(project, strict=True)

    assert set(app.resources) == {"worker"}


@pytest.mark.parametrize("filename", ["broken.py", "my-scripts/broken.py"])
def test_scan_failure_suggests_runpodignore(tmp_path, filename):
    write_app(tmp_path)
    target = tmp_path / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("raise RuntimeError('not application code')\n")

    with pytest.raises(DiscoveryError, match=r"add it to \.runpodignore"):
        discover_apps(tmp_path, strict=True)


def test_stdlib_collision_suggests_renaming_project_module(tmp_path):
    original = importlib.import_module("queue")
    (tmp_path / "queue.py").write_text("raise AssertionError('must not import')\n")

    with pytest.raises(DiscoveryError) as exc:
        discover_apps(tmp_path, strict=True)

    assert "Rename the project module" in str(exc.value)
    assert "shadowing an installed or standard-library module" in str(exc.value)
    assert str(tmp_path / "queue.py") in str(exc.value)
    assert sys.modules["queue"] is original


def test_flat_import_in_package_names_root_initializer(tmp_path):
    package = tmp_path / "feedback_package"
    write_app(package)
    (package / "__init__.py").write_text("")
    (package / "worker.py").write_text("from feedback_app import app\n")

    with pytest.raises(DiscoveryError) as exc:
        discover_apps(package, strict=True)

    assert str(package / "__init__.py") in str(exc.value)
    assert "package-relative imports" in str(exc.value)


def test_invalid_package_name_identifies_initializer(tmp_path):
    package = tmp_path / "my-svc"
    write_app(package)
    (package / "__init__.py").write_text("")

    with pytest.raises(DiscoveryError) as exc:
        discover_apps(package, strict=True)

    assert str(package / "__init__.py") in str(exc.value)
    assert "not a valid python identifier" in str(exc.value)


def test_discovery_preserves_project_path_for_lazy_import(tmp_path):
    project = tmp_path / "project"
    target = write_app(project)
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "feedback_vendor.py").write_text("VALUE = 42\n")
    target.write_text(
        "import sys\n"
        f"sys.path.append({str(vendor)!r})\n"
        "from runpod import App\n"
        "app = App('svc')\n"
        "@app.queue(cpu='cpu3c-1-2')\n"
        "def worker():\n"
        "    from feedback_vendor import VALUE\n"
        "    return VALUE\n"
    )
    previous_path = sys.path[:]

    (app,) = discover_apps(project, strict=True)

    assert sys.path == [*previous_path, str(vendor)]
    assert app.resources["worker"].local() == 42


@pytest.mark.parametrize("fails", [False, True])
def test_discovery_preserves_preexisting_import_path(tmp_path, fails):
    target = write_app(tmp_path)
    if fails:
        target.write_text("raise RuntimeError('broken')\n")
    sys.path.append(str(tmp_path))
    previous_path = sys.path[:]

    if fails:
        with pytest.raises(DiscoveryError):
            discover_apps(tmp_path, strict=True)
    else:
        discover_apps(tmp_path, strict=True)

    assert sys.path == previous_path
