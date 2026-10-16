"""tests for deploy-time environment vendoring."""

import subprocess
import sys
import threading
from unittest.mock import patch

import pytest

from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.build import (
    SIZE_PROHIBITIVE_PACKAGES,
    BuildError,
    collect_requirements,
    prune_excluded,
    requirement_name,
    runpod_requirement,
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

    def test_auto_exclude_off_vendors_everything(self):
        # custom images carry no torch guarantee: nothing auto-excluded
        kept, excluded = split_exclusions(
            ["numpy", "torch==2.4", "triton"], auto_exclude=False
        )
        assert kept == ["numpy", "torch==2.4", "triton"]
        assert excluded == []

    def test_auto_exclude_off_user_excludes_still_apply(self):
        kept, excluded = split_exclusions(
            ["numpy", "torch"], extra_excludes=["torch"], auto_exclude=False
        )
        assert kept == ["numpy"]
        assert excluded == ["torch"]

    def test_exclusion_set_is_size_based(self):
        # sanity-pin the auto set; additions must be size-justified
        assert SIZE_PROHIBITIVE_PACKAGES == {
            "torch",
            "torchvision",
            "torchaudio",
            "triton",
        }


class TestRunpodRequirement:
    def test_default_is_published_package(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RUNPOD_PACKAGE_SPEC", raising=False)
        assert runpod_requirement(tmp_path) == "runpod"

    def test_running_package_overlays_env(self, tmp_path):
        from runpod.apps.build import sync_running_package

        env_dir = tmp_path / "env"
        # stale copy from the pypi install: must be overwritten
        (env_dir / "runpod").mkdir(parents=True)
        (env_dir / "runpod" / "stale.py").write_text("old = True")

        sync_running_package(env_dir)

        # the client's sdk tree wins
        assert (env_dir / "runpod" / "apps" / "build.py").is_file()
        assert not any(
            p.name == "__pycache__"
            for p in (env_dir / "runpod").rglob("*")
            if p.is_dir()
        )

    def test_version_pin_passes_through(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNPOD_PACKAGE_SPEC", "runpod==1.8.0")
        assert runpod_requirement(tmp_path) == "runpod==1.8.0"

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
            assert runpod_requirement(tmp_path) == str(wheel)


def _fake_popen(captured, *, returncode=0, stdout="", stderr=""):
    import io

    def fake(cmd, **kwargs):
        captured["cmd"] = cmd

        class P:
            def __init__(self):
                self.stdout = io.StringIO(stdout + stderr)
                self.returncode = returncode

            def wait(self):
                return returncode

        return P()

    return fake


class TestVendor:
    @pytest.fixture(autouse=True)
    def pip_available(self):
        with patch("runpod.apps.build._ensure_pip"):
            yield

    def test_failure_raises_build_error(self, tmp_path):
        with patch(
            "runpod.apps.build.subprocess.Popen",
            side_effect=_fake_popen(
                {}, returncode=1, stderr="no matching distribution"
            ),
        ):
            with pytest.raises(BuildError, match="no matching distribution"):
                vendor(tmp_path, ["nonexistent-xyz"], "3.12")

    def test_progress_reports_collected_packages(self, tmp_path):
        seen = []
        stdout = (
            "Collecting numpy>=1.0\n"
            "  Downloading numpy-2.0-cp312.whl\n"
            "Collecting requests\n"
            "Installing collected packages\n"
        )
        with patch(
            "runpod.apps.build.subprocess.Popen",
            side_effect=_fake_popen({}, stdout=stdout),
        ):
            vendor(
                tmp_path,
                ["numpy"],
                "3.12",
                progress=lambda c, n: seen.append((c, n)),
            )
        assert seen == [(1, "numpy"), (2, "requests")]

    def test_large_stderr_does_not_block_progress_or_error(self, tmp_path):
        original_popen = subprocess.Popen
        processes = []
        expired = threading.Event()
        script = (
            "import sys; "
            "sys.stderr.write('x' * 262144 + '\\n'); "
            "sys.stderr.flush(); "
            "print('Collecting numpy'); "
            "print('resolver failed', file=sys.stderr); "
            "sys.exit(1)"
        )

        def launch(_cmd, **kwargs):
            process = original_popen(
                [sys.executable, "-c", script],
                **kwargs,
            )
            processes.append(process)
            return process

        def stop_blocked_process():
            expired.set()
            for process in processes:
                process.kill()

        timer = threading.Timer(10, stop_blocked_process)
        seen = []
        timer.start()
        try:
            with patch("runpod.apps.build.subprocess.Popen", side_effect=launch):
                with pytest.raises(BuildError, match="resolver failed"):
                    vendor(
                        tmp_path,
                        ["numpy"],
                        "3.12",
                        progress=lambda count, name: seen.append((count, name)),
                    )
        finally:
            timer.cancel()
            for process in processes:
                process.wait()
        assert not expired.is_set()
        assert seen == [(1, "numpy")]


def _wheel(env, name, version="1.0", requires=(), files=(), record=True, metadata=True):
    """fake a pip --target install of one wheel into env."""
    dist_info = env / f"{name.replace('-', '_')}-{version}.dist-info"
    dist_info.mkdir(parents=True)
    if metadata:
        lines = [f"Name: {name}", f"Version: {version}"]
        lines += [f"Requires-Dist: {r}" for r in requires]
        (dist_info / "METADATA").write_text("\n".join(lines) + "\n")
    rel_files = list(files) or [f"{name.replace('-', '_')}/__init__.py"]
    for rel in rel_files:
        target = env / (rel[len("../../"):] if rel.startswith("../../") else rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
    if record:
        rows = rel_files + [f"{dist_info.name}/METADATA", f"{dist_info.name}/RECORD"]
        (dist_info / "RECORD").write_text("".join(f"{r},,\n" for r in rows))


def _installed(env):
    return sorted(d.name.split("-")[0] for d in env.glob("*.dist-info"))


TORCH = sorted(SIZE_PROHIBITIVE_PACKAGES)


class TestPruneExcluded:
    def _peft_env(self, env):
        _wheel(env, "peft", requires=["torch>=1.13", "numpy"])
        _wheel(env, "numpy")
        _wheel(
            env,
            "torch",
            requires=[
                "sympy",
                "numpy",
                'nvidia-cublas-cu12==12.8; platform_system == "Linux"',
            ],
            files=[
                "torch/__init__.py",
                "torch/__pycache__/__init__.cpython-312.pyc",
                "functorch/__init__.py",
                "torchgen/__init__.py",
                "../../bin/torchrun",
            ],
        )
        _wheel(env, "sympy", requires=["mpmath"])
        _wheel(env, "mpmath")
        _wheel(
            env,
            "nvidia-cublas-cu12",
            files=["nvidia/cublas/lib/libcublas.so", "nvidia/__init__.py"],
        )
        _wheel(
            env,
            "nvidia-cudnn-cu12",
            files=["nvidia/cudnn/lib/libcudnn.so", "nvidia/__init__.py"],
        )

    def test_transitive_torch_and_its_tree_removed(self, tmp_path):
        self._peft_env(tmp_path)
        _wheel(tmp_path, "faster-whisper", requires=["nvidia-cudnn-cu12"])

        dropped = prune_excluded(
            tmp_path, TORCH, ["peft", "faster-whisper"], TORCH
        )

        assert dropped == ["mpmath", "nvidia-cublas-cu12", "sympy", "torch"]
        assert _installed(tmp_path) == [
            "faster_whisper",
            "numpy",
            "nvidia_cudnn_cu12",
            "peft",
        ]
        for gone in ("torch", "functorch", "torchgen", "sympy", "bin/torchrun"):
            assert not (tmp_path / gone).exists(), gone
        # shared namespace dir keeps the files a kept wheel installed
        assert (tmp_path / "nvidia/cudnn/lib/libcudnn.so").is_file()
        assert (tmp_path / "nvidia/__init__.py").is_file()
        assert not (tmp_path / "nvidia/cublas").exists()

    def test_root_requirement_is_kept(self, tmp_path):
        self._peft_env(tmp_path)
        prune_excluded(tmp_path, TORCH, ["peft", "sympy==1.14"], TORCH)
        assert "sympy" in _installed(tmp_path)
        assert "mpmath" in _installed(tmp_path)

    def test_nothing_excluded_installed_is_noop(self, tmp_path):
        _wheel(tmp_path, "numpy")
        assert prune_excluded(tmp_path, TORCH, ["numpy"], TORCH) == []
        assert _installed(tmp_path) == ["numpy"]

    def test_missing_record_fails_closed(self, tmp_path):
        self._peft_env(tmp_path)
        _wheel(tmp_path, "oddball", record=False)
        dropped = prune_excluded(tmp_path, TORCH, ["peft", "oddball"], TORCH)
        # only the excluded package itself goes
        assert dropped == ["torch"]
        assert not (tmp_path / "torch").exists()
        assert "sympy" in _installed(tmp_path)

    def test_missing_record_on_excluded_falls_back_to_dir(self, tmp_path):
        _wheel(tmp_path, "peft", requires=["torch"])
        _wheel(tmp_path, "torch", record=False)
        assert prune_excluded(tmp_path, TORCH, ["peft"], TORCH) == ["torch"]
        assert not (tmp_path / "torch").exists()

    def test_unnamed_root_fails_closed(self, tmp_path):
        self._peft_env(tmp_path)
        dropped = prune_excluded(
            tmp_path, TORCH, ["peft", "https://example.com/pkg.tar.gz"], TORCH
        )
        assert dropped == ["torch"]

    def test_unreadable_requires_dist_fails_closed(self, tmp_path):
        self._peft_env(tmp_path)
        _wheel(tmp_path, "weird", requires=["https://example.com/x.whl"])
        dropped = prune_excluded(tmp_path, TORCH, ["peft", "weird"], TORCH)
        assert dropped == ["torch"]

    def test_user_exclude_keeps_its_dependencies(self, tmp_path):
        self._peft_env(tmp_path)
        _wheel(tmp_path, "bigpkg", requires=["sympy"])
        dropped = prune_excluded(
            tmp_path, TORCH + ["bigpkg"], ["peft", "bigpkg"], TORCH
        )
        assert "bigpkg" in dropped
        # sympy is torch-only except that bigpkg needs it
        assert "sympy" in _installed(tmp_path)

    def test_no_image_provided_removes_only_named(self, tmp_path):
        # custom image: a user --exclude of torch drops torch, not its tree
        self._peft_env(tmp_path)
        assert prune_excluded(tmp_path, ["torch"], ["peft"], []) == ["torch"]
        assert "sympy" in _installed(tmp_path)


class TestBuildEnvironmentPrunes:
    def _app(self, **kwargs):
        app = App("a")

        @app.queue(name="q", dependencies=["peft"], **kwargs)
        def q():
            pass

        return app

    def _fake_vendor(self, env_dir, requirements, python_version, **kwargs):
        if "peft" in requirements:
            _wheel(env_dir, "peft", requires=["torch"])
            _wheel(env_dir, "torch", requires=["sympy"])
            _wheel(env_dir, "sympy")

    def test_gpu_builtin_image_prunes_transitive_torch(self, tmp_path):
        from runpod.apps.build import build_environment

        with patch("runpod.apps.build.vendor", side_effect=self._fake_vendor):
            result = build_environment(
                self._app(gpu="AMPERE_16"), tmp_path, scratch_dir=tmp_path / "s"
            )
        assert result.excluded == ["torch"]
        assert _installed(result.env_dir) == ["peft"]

    def test_mixed_cpu_gpu_app_prunes_transitive_torch(self, tmp_path):
        from runpod.apps.build import build_environment

        app = self._app(gpu="AMPERE_16")

        @app.queue(name="prep", cpu="cpu3c-1-2", dependencies=["pandas"])
        def prep():
            pass

        with patch("runpod.apps.build.vendor", side_effect=self._fake_vendor):
            result = build_environment(app, tmp_path, scratch_dir=tmp_path / "s")
        assert result.excluded == ["torch"]
        assert _installed(result.env_dir) == ["peft"]

    def test_custom_image_keeps_transitive_torch(self, tmp_path):
        from runpod.apps.build import build_environment

        app = self._app(gpu="AMPERE_16", image="me/custom:1")
        with patch("runpod.apps.build.vendor", side_effect=self._fake_vendor):
            result = build_environment(app, tmp_path, scratch_dir=tmp_path / "s")
        assert result.excluded == []
        assert _installed(result.env_dir) == ["peft", "sympy", "torch"]

    def test_cpu_keeps_transitive_torch(self, tmp_path):
        from runpod.apps.build import build_environment

        with patch("runpod.apps.build.vendor", side_effect=self._fake_vendor):
            result = build_environment(
                self._app(cpu="cpu3c-1-2"), tmp_path, scratch_dir=tmp_path / "s"
            )
        assert result.excluded == []
        assert _installed(result.env_dir) == ["peft", "sympy", "torch"]
