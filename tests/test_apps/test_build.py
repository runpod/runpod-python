"""tests for deploy-time environment vendoring."""

from pathlib import Path
from unittest.mock import patch

import pytest

from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.build import (
    SIZE_PROHIBITIVE_PACKAGES,
    BuildError,
    collect_requirements,
    requirement_name,
    runtime_requirement,
    split_exclusions,
    vendor,
)


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


class TestRequirementName:
    @pytest.mark.parametrize(
        "spec,name",
        [
            ("numpy", "numpy"),
            ("numpy==1.26.0", "numpy"),
            ("Torch>=2.0", "torch"),
            ("scikit_learn~=1.4", "scikit-learn"),
            ("pillow[extras]==10", "pillow"),
        ],
    )
    def test_extracts_distribution_name(self, spec, name):
        assert requirement_name(spec) == name


class TestCollectRequirements:
    def test_merges_requirements_txt_and_decorator_deps(self, tmp_path):
        (tmp_path / "requirements.txt").write_text(
            "numpy==1.26\n# comment\n\npandas\n"
        )
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2", dependencies=["requests", "numpy"])
        def q():
            pass

        reqs = collect_requirements(tmp_path, app)
        # numpy deduped by name, requirements.txt wins on order
        assert reqs == ["numpy==1.26", "pandas", "requests"]

    def test_no_requirements_file(self, tmp_path):
        app = App("a")

        @app.queue(name="q", cpu="cpu3c-1-2")
        def q():
            pass

        assert collect_requirements(tmp_path, app) == []


class TestSplitExclusions:
    def test_torch_family_auto_excluded(self):
        kept, excluded = split_exclusions(
            ["numpy", "torch==2.4", "torchvision", "requests"]
        )
        assert kept == ["numpy", "requests"]
        assert excluded == ["torch", "torchvision"]

    def test_user_excludes_added(self):
        kept, excluded = split_exclusions(
            ["numpy", "transformers"], extra_excludes=["transformers"]
        )
        assert kept == ["numpy"]
        assert excluded == ["transformers"]

    def test_exclusion_set_is_size_based(self):
        # sanity-pin the auto set; additions must be size-justified
        assert SIZE_PROHIBITIVE_PACKAGES == {
            "torch",
            "torchvision",
            "torchaudio",
            "triton",
        }


class TestRuntimeRequirement:
    def test_default_is_published_package(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RUNPOD_PACKAGE_SPEC", raising=False)
        assert runtime_requirement(tmp_path) == "runpod"

    def test_version_pin_passes_through(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNPOD_PACKAGE_SPEC", "runpod==1.8.0")
        assert runtime_requirement(tmp_path) == "runpod==1.8.0"

    def test_url_spec_builds_wheel(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "RUNPOD_PACKAGE_SPEC", "https://example.com/runpod.tar.gz"
        )
        wheel = tmp_path / "wheels" / "runpod-0.0.0.dev0-py3-none-any.whl"

        def fake_run(cmd, **kwargs):
            wheel.parent.mkdir(parents=True, exist_ok=True)
            wheel.write_bytes(b"")

            class R:
                returncode = 0
                stderr = ""

            return R()

        with patch("runpod.apps.build.subprocess.run", side_effect=fake_run):
            assert runtime_requirement(tmp_path) == str(wheel)


class TestVendor:
    def test_targets_worker_platform(self, tmp_path):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd

            class R:
                returncode = 0
                stderr = ""

            return R()

        with patch("runpod.apps.build.subprocess.run", side_effect=fake_run):
            vendor(tmp_path, ["numpy"], "3.12")

        cmd = captured["cmd"]
        assert "--target" in cmd
        assert "--python-version" in cmd and "3.12" in cmd
        assert "--only-binary" in cmd
        assert "manylinux_2_28_x86_64" in cmd

    def test_empty_requirements_no_op(self, tmp_path):
        with patch("runpod.apps.build.subprocess.run") as run:
            vendor(tmp_path, [], "3.12")
        run.assert_not_called()

    def test_failure_raises_build_error(self, tmp_path):
        def fake_run(cmd, **kwargs):
            class R:
                returncode = 1
                stderr = "no matching distribution"

            return R()

        with patch("runpod.apps.build.subprocess.run", side_effect=fake_run):
            with pytest.raises(BuildError, match="no matching distribution"):
                vendor(tmp_path, ["nonexistent-xyz"], "3.12")
