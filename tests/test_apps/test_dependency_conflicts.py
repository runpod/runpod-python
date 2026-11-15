"""dependency constraints shared by an app's resources."""

from unittest.mock import patch

import pytest

from runpod.apps import App
from runpod.apps.app import _clear_registry
from runpod.apps.build import BuildError, build_environment, collect_requirements


@pytest.fixture(autouse=True)
def clean_registry():
    _clear_registry()
    yield
    _clear_registry()


def app_with_dependencies(*dependencies):
    app = App("dependencies")
    for index, deps in enumerate(dependencies):

        @app.queue(name=f"worker-{index}", cpu="cpu3c-1-2", dependencies=deps)
        def worker():
            pass

    return app


@pytest.mark.parametrize("project_pin", [False, True])
def test_conflicting_pins_report_both_sources_before_vendoring(tmp_path, project_pin):
    if project_pin:
        (tmp_path / "requirements.txt").write_text("numpy==1.26.4\n")
        app = app_with_dependencies(["numpy==2.1.3"])
        sources = ["requirements.txt", "worker-0"]
    else:
        app = app_with_dependencies(["numpy==1.26.4"], ["numpy==2.1.3"])
        sources = ["worker-0", "worker-1"]

    with patch("runpod.apps.build.vendor") as vendor:
        with pytest.raises(
            BuildError, match="conflicting dependency requirements"
        ) as exc:
            build_environment(app, tmp_path, scratch_dir=tmp_path / "scratch")

    vendor.assert_not_called()
    for detail in ["numpy==1.26.4", "numpy==2.1.3", *sources]:
        assert detail in str(exc.value)


@pytest.mark.parametrize(
    "requirements",
    [
        ["Some_Package==1", "some.package==2"],
        ["numpy==1.26.4", "numpy>=2"],
        ["numpy>=2", "numpy==1.26.4"],
        ["numpy==1.26.4", "numpy!=1.26.4"],
        ["numpy==1.26.*", "numpy==2.1.3"],
        ["numpy==1.26.4,==2.1.3"],
        ["torch==2.4", "torch==2.5"],
    ],
)
def test_incompatible_unconditional_constraints_fail(tmp_path, requirements):
    app = app_with_dependencies(requirements)
    with pytest.raises(BuildError, match="conflicting dependency requirements"):
        collect_requirements(tmp_path, app)


def test_conflicting_pins_in_requirements_file_fail(tmp_path):
    (tmp_path / "requirements.txt").write_text("numpy==1.26.4\nnumpy==2.1.3\n")
    with pytest.raises(BuildError, match="requirements.txt"):
        collect_requirements(tmp_path, App("project"))


@pytest.mark.parametrize(
    "requirements",
    [
        ["numpy", "numpy==1.26.4"],
        ["numpy>=1", "numpy<3"],
        ["numpy==1.26.4", "numpy>=1,<2"],
        ["numpy==1.26", "numpy==1.26.0"],
        ["numpy==1.26", "numpy==1.26+local"],
        ["numpy==1.26+local", "numpy==1.26"],
        ["numpy==2.0rc1", "numpy>=2.0rc1"],
        ["requests", "requests[socks]"],
        ["requests[socks]", "requests[security]"],
        [
            'numpy==1.26.4; python_version < "3.12"',
            'numpy==2.1.3; python_version >= "3.12"',
        ],
        ['numpy==1.26.4; sys_platform == "darwin"', "numpy"],
        ["numpy==1.26.4", 'numpy==2.1.3; sys_platform == "darwin"'],
        ["numpy @ https://example.com/numpy.whl", "numpy>=1"],
        ["https://example.com/a.whl", "https://example.com/b.whl"],
    ],
)
def test_distinct_compatible_or_conditional_requirements_reach_resolver(
    tmp_path, requirements
):
    app = app_with_dependencies(*[[req] for req in requirements])
    assert collect_requirements(tmp_path, app) == requirements
    with patch("runpod.apps.build.vendor") as vendor, patch(
        "runpod.apps.build.sync_running_package"
    ):
        result = build_environment(app, tmp_path, scratch_dir=tmp_path / "scratch")

    assert result.requirements[-len(requirements) :] == requirements
    assert vendor.call_args.args[1][-len(requirements) :] == requirements


def test_identical_requirements_are_deduplicated(tmp_path):
    app = app_with_dependencies(["numpy==1.26.4"], ["numpy==1.26.4"])
    assert collect_requirements(tmp_path, app) == ["numpy==1.26.4"]


def test_conflicting_excluded_pins_fail(tmp_path):
    app = app_with_dependencies(["torch==2.4"], ["torch==2.5"])
    with patch("runpod.apps.build.vendor") as vendor:
        with pytest.raises(BuildError, match="torch"):
            build_environment(
                app, tmp_path, exclude=["torch"], scratch_dir=tmp_path / "scratch"
            )
    vendor.assert_not_called()
