"""Test the version module.

deploy without RUNPOD_PACKAGE_SPEC overlays the client's runpod package tree
onto the artifact (sync_running_package) without its dist-info, while the
worker image carries its own installed runpod dist. These tests reproduce that
layout in a subprocess and check which version the bundled code reports.
"""

import json
import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

import pytest

from runpod.apps.build import sync_running_package
from runpod.version import get_version

IMAGE_VERSION = "2.0.0"  # runpod dist preinstalled in the worker image
BUNDLED_VERSION = "9.9.9"  # version of the client build copied into the artifact
SUBPROCESS_TIMEOUT = 120  # seconds

# the dev venv installs runpod editable; its setuptools finder resolves any
# runpod.* module missing from the artifact copy back to the source checkout.
# worker images install a regular wheel, so the probe drops that finder.
PROBE = """
import json
import sys
sys.meta_path[:] = [
    f for f in sys.meta_path if "__editable__" not in getattr(f, "__module__", "")
]
from importlib.metadata import version
import runpod
import runpod.serverless
print(json.dumps({
    "file": runpod.__file__,
    "dunder_version": runpod.__version__,
    "banner_version": runpod.serverless.runpod_version,
    "installed_dist": version("runpod"),
}))
"""


def _write_image_dist(site_dir: Path, dist_version: str) -> None:
    """Install-like metadata for a runpod dist, without its package code."""
    dist_info = site_dir / f"runpod-{dist_version}.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: runpod\nVersion: {dist_version}\n"
    )


def _write_generated_version(package_dir: Path, value: str) -> None:
    """Stand-in for the runpod/_version.py setuptools-scm writes at build time."""
    (package_dir / "_version.py").write_text(f"__version__ = version = {value!r}\n")


def _probe(*path_entries: Path) -> dict:
    """Import runpod with path_entries ahead of the venv, like the worker does.

    runs from the first path entry: `python -c` puts the cwd on sys.path
    first, and the repo root would shadow the artifact copy.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in path_entries)
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env=env,
        cwd=path_entries[0],
        timeout=SUBPROCESS_TIMEOUT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture
def worker_layout(tmp_path: Path) -> tuple[Path, Path]:
    """(artifact_dir, image_site_dir): a copied client tree over an older image dist."""
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    sync_running_package(artifact_dir)
    _write_generated_version(artifact_dir / "runpod", BUNDLED_VERSION)

    image_site = tmp_path / "image-site-packages"
    _write_image_dist(image_site, IMAGE_VERSION)
    return artifact_dir, image_site


def test_bundled_tree_reports_its_own_version(worker_layout):
    """the worker reports the bundled build, not the image's installed dist."""
    artifact_dir, image_site = worker_layout

    report = _probe(artifact_dir, image_site)

    assert Path(report["file"]).is_relative_to(artifact_dir)
    assert report["installed_dist"] == IMAGE_VERSION
    assert report["dunder_version"] == BUNDLED_VERSION


def test_worker_banner_reports_bundled_version(worker_layout):
    """the serverless start banner uses the bundled build's version."""
    artifact_dir, image_site = worker_layout

    report = _probe(artifact_dir, image_site)

    assert report["banner_version"] == BUNDLED_VERSION


def test_tree_without_generated_version_uses_installed_dist(worker_layout):
    """a tree without runpod/_version.py (e.g. an uninstalled checkout)
    falls back to the installed dist metadata."""
    artifact_dir, image_site = worker_layout
    (artifact_dir / "runpod" / "_version.py").unlink()

    report = _probe(artifact_dir, image_site)

    assert report["dunder_version"] == IMAGE_VERSION


def test_version_unknown_without_any_source():
    """no generated module and no installed dist reports 'unknown'."""
    with (
        patch.dict(sys.modules, {"runpod._version": None}),
        patch("runpod.version.version", side_effect=PackageNotFoundError),
    ):
        assert get_version() == "unknown"
