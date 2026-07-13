"""unit tests for the shared FunctionRequest execution engine."""

import pytest

from runpod.runtimes import executor


def test_execute_roundtrip_json():
    response = executor.execute_request(
        {
            "function_name": "mul",
            "function_code": "def mul(a, b):\n    return a * b",
            "args": [6, 7],
            "kwargs": {},
            "serialization_format": "json",
        }
    )
    assert response["success"] is True
    assert response["json_result"] == 42


def test_json_result_rejects_non_json_return():
    with pytest.raises(TypeError, match="json-serializable"):
        executor._serialize_result(object(), "json")


def test_serialize_chunk_marks_stream():
    chunk = executor.serialize_chunk("tok", {"serialization_format": "json"})
    assert chunk == {"success": True, "__stream__": True, "json_result": "tok"}


def test_resolve_request_returns_callable():
    prepared, error = executor.resolve_request(
        {
            "function_name": "double",
            "function_code": "def double(x):\n    return x * 2",
            "kwargs": {},
            "args": [],
            "serialization_format": "json",
        }
    )
    assert error is None
    fn, args, kwargs = prepared
    assert fn(4) == 8


def test_resolve_request_missing_function():
    prepared, error = executor.resolve_request(
        {"function_name": "nope", "function_code": "x = 1"}
    )
    assert prepared is None
    assert error["success"] is False
    assert "not found" in error["error"]


def test_generator_results_aggregate():
    response = executor.execute_request(
        {
            "function_name": "gen",
            "function_code": "def gen(n):\n    yield from range(n)",
            "args": [],
            "kwargs": {"n": 3},
            "serialization_format": "json",
        }
    )
    assert response["success"] is True
    assert response["json_result"] == [0, 1, 2]


class TestSystemDependencies:
    def test_missing_apt_reports_error(self, monkeypatch):
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _: None)
        response = executor.execute_request(
            {
                "function_name": "f",
                "function_code": "def f():\n    return 1",
                "system_dependencies": ["ffmpeg"],
                "serialization_format": "json",
            }
        )
        assert response["success"] is False
        assert "apt-get is not available" in response["error"]

    def test_system_deps_installed_before_execution(self, monkeypatch):
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/apt-get")
        monkeypatch.setattr(executor, "_apt_updated", executor.threading.Event())
        calls = []

        class R:
            returncode = 0
            stderr = ""

        monkeypatch.setattr(
            executor.subprocess,
            "run",
            lambda cmd, **kw: calls.append(cmd) or R(),
        )
        response = executor.execute_request(
            {
                "function_name": "f",
                "function_code": "def f():\n    return 40 + 2",
                "system_dependencies": ["ffmpeg"],
                "serialization_format": "json",
            }
        )
        assert response["success"] is True
        assert response["json_result"] == 42
        assert calls[0][:2] == ["apt-get", "update"]
        assert "ffmpeg" in calls[1]

    def test_apt_update_runs_once(self, monkeypatch):
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/apt-get")
        monkeypatch.setattr(executor, "_apt_updated", executor.threading.Event())
        calls = []

        class R:
            returncode = 0
            stderr = ""

        monkeypatch.setattr(
            executor.subprocess,
            "run",
            lambda cmd, **kw: calls.append(cmd) or R(),
        )
        executor._install_system(["ffmpeg"])
        executor._install_system(["sox"])
        updates = [c for c in calls if c[:2] == ["apt-get", "update"]]
        assert len(updates) == 1


class TestStdoutTee:
    def test_prints_reach_real_stdout_and_response(self, capfd):
        response = executor.execute_request(
            {
                "function_name": "speak",
                "function_code": "def speak():\n    print('live line')\n    return 1\n",
                "args": [],
                "kwargs": {},
                "serialization_format": "json",
            }
        )
        assert response["success"]
        # captured for the job response
        assert "live line" in response["stdout"]
        # and written through to the container's stdout for log streams
        assert "live line" in capfd.readouterr().out


class TestCloudpickleLoading:
    def test_available_returns_module(self):
        module = executor._load_cloudpickle()
        assert module is not None
        assert hasattr(module, "dumps")

    def test_missing_without_install_returns_none(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def no_cloudpickle(name, *args, **kwargs):
            if name == "cloudpickle":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_cloudpickle)
        assert executor._load_cloudpickle(install=False) is None


class TestInstallHelpers:
    def test_install_empty_is_noop(self):
        assert executor._install([], "nothing") is None

    def test_install_failure_returns_message(self, monkeypatch):
        from unittest.mock import MagicMock

        result = MagicMock(returncode=1, stderr="resolver exploded")
        monkeypatch.setattr(
            executor.subprocess, "run", MagicMock(return_value=result)
        )
        message = executor._install(["ghost-package"], "deps")
        assert "resolver exploded" in message

    def test_install_system_requires_apt(self, monkeypatch):
        monkeypatch.setattr(executor.shutil, "which", lambda _: None)
        message = executor._install_system(["ffmpeg"])
        assert "apt-get" in message

    def test_install_system_empty_is_noop(self):
        assert executor._install_system([]) is None
