"""rp update and the passive update check."""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from runpod.rp_cli import update as upd
from runpod.rp_cli.main import cli


class TestParseVersion:
    def test_release(self):
        assert upd.parse_version("1.8.0") == (1, 8, 0)

    def test_dev_suffix_ignored(self):
        assert upd.parse_version("1.8.0.dev3") == (1, 8, 0)

    def test_two_part(self):
        assert upd.parse_version("2.0") == (2, 0)


class TestCompareVersions:
    def test_orders(self):
        assert upd.compare_versions((1, 0), (2, 0)) < 0
        assert upd.compare_versions((2, 1), (2, 0)) > 0

    def test_pads_shorter(self):
        assert upd.compare_versions((2, 0), (2, 0, 0)) == 0


class TestInstallCommand:
    def test_prefers_uv(self):
        with patch.object(upd.shutil, "which", return_value="/usr/bin/uv"):
            cmd = upd.install_command("1.2.3")
        assert cmd[0] == "uv"
        assert "runpod==1.2.3" in cmd

    def test_falls_back_to_pip(self):
        with patch.object(upd.shutil, "which", return_value=None):
            cmd = upd.install_command("1.2.3")
        assert cmd[1:4] == ["-m", "pip", "install"]


class TestUpdateCommand:
    def test_already_current(self):
        runner = CliRunner()
        with patch.object(
            upd, "fetch_pypi_metadata", return_value=("9.9.9", {"9.9.9"})
        ), patch.object(upd, "current_version", return_value="9.9.9"), patch(
            "runpod.rp_cli.main.cli.callback"
        ):
            result = runner.invoke(cli, ["update"])
        assert result.exit_code == 0, result.output
        assert "nothing to do" in result.output

    def test_unknown_version_fails(self):
        runner = CliRunner()
        with patch.object(
            upd, "fetch_pypi_metadata", return_value=("9.9.9", {"9.9.9"})
        ):
            result = runner.invoke(cli, ["update", "--version", "0.0.0.404"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_installs_target(self):
        runner = CliRunner()
        with patch.object(
            upd, "fetch_pypi_metadata", return_value=("9.9.9", {"9.9.9"})
        ), patch.object(upd, "current_version", return_value="1.0.0"), patch.object(
            upd, "run_install"
        ) as install:
            result = runner.invoke(cli, ["update"])
        assert result.exit_code == 0, result.output
        install.assert_called_once_with("9.9.9")


class TestBackgroundCheckCache:
    def test_fresh_cache_skips_fetch(self, tmp_path, monkeypatch):
        cache = tmp_path / "update_check.json"
        monkeypatch.setattr(upd, "CACHE_PATH", cache)
        from datetime import datetime, timezone

        cache.write_text(
            json.dumps(
                {
                    "last_checked_utc": datetime.now(timezone.utc).isoformat(),
                    "latest_version": "99.0.0",
                }
            )
        )
        monkeypatch.setattr(upd, "_newer_version", None)
        with patch.object(upd, "fetch_pypi_metadata") as fetch, patch.object(
            upd, "current_version", return_value="1.0.0"
        ):
            upd._run_check()
        fetch.assert_not_called()
        assert upd._newer_version == "99.0.0"

    def test_stale_cache_refetches(self, tmp_path, monkeypatch):
        cache = tmp_path / "update_check.json"
        monkeypatch.setattr(upd, "CACHE_PATH", cache)
        cache.write_text(
            json.dumps(
                {
                    "last_checked_utc": "2000-01-01T00:00:00+00:00",
                    "latest_version": "0.1.0",
                }
            )
        )
        monkeypatch.setattr(upd, "_newer_version", None)
        with patch.object(
            upd, "fetch_pypi_metadata", return_value=("99.0.0", {"99.0.0"})
        ), patch.object(upd, "current_version", return_value="1.0.0"):
            upd._run_check()
        assert upd._newer_version == "99.0.0"
        assert json.loads(cache.read_text())["latest_version"] == "99.0.0"

    def test_current_version_newer_no_notice(self, tmp_path, monkeypatch):
        monkeypatch.setattr(upd, "CACHE_PATH", tmp_path / "update_check.json")
        monkeypatch.setattr(upd, "_newer_version", None)
        with patch.object(
            upd, "fetch_pypi_metadata", return_value=("1.0.0", {"1.0.0"})
        ), patch.object(upd, "current_version", return_value="2.0.0"):
            upd._run_check()
        assert upd._newer_version is None

    def test_check_never_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(upd, "CACHE_PATH", tmp_path / "update_check.json")
        with patch.object(
            upd, "fetch_pypi_metadata", side_effect=RuntimeError("boom")
        ), patch.object(upd, "current_version", return_value="1.0.0"):
            upd._run_check()


class TestFetchPypiMetadata:
    def _response(self, payload: bytes):
        import contextlib
        import io

        @contextlib.contextmanager
        def _cm(url, timeout=None):
            yield io.BytesIO(payload)

        return _cm

    def test_parses_metadata(self, monkeypatch):
        import json as _json

        payload = _json.dumps(
            {"info": {"version": "1.9.0"}, "releases": {"1.8.0": [], "1.9.0": []}}
        ).encode()
        monkeypatch.setattr(
            upd.urllib.request, "urlopen", self._response(payload)
        )
        latest, releases = upd.fetch_pypi_metadata()
        assert latest == "1.9.0"
        assert releases == {"1.8.0", "1.9.0"}

    def test_network_error(self, monkeypatch):
        import urllib.error

        def _raise(url, timeout=None):
            raise urllib.error.URLError("no route")

        monkeypatch.setattr(upd.urllib.request, "urlopen", _raise)
        with pytest.raises(ConnectionError, match="could not reach pypi"):
            upd.fetch_pypi_metadata()

    def test_http_error(self, monkeypatch):
        import urllib.error

        def _raise(url, timeout=None):
            raise urllib.error.HTTPError(url, 503, "unavailable", {}, None)

        monkeypatch.setattr(upd.urllib.request, "urlopen", _raise)
        with pytest.raises(RuntimeError, match="HTTP 503"):
            upd.fetch_pypi_metadata()

    def test_garbage_response(self, monkeypatch):
        monkeypatch.setattr(
            upd.urllib.request, "urlopen", self._response(b"not json")
        )
        with pytest.raises(RuntimeError, match="unexpected response"):
            upd.fetch_pypi_metadata()

    def test_missing_version_key(self, monkeypatch):
        monkeypatch.setattr(
            upd.urllib.request, "urlopen", self._response(b"{}")
        )
        with pytest.raises(RuntimeError, match="missing version"):
            upd.fetch_pypi_metadata()


class TestRunInstall:
    def test_success(self, monkeypatch):
        from unittest.mock import MagicMock

        result = MagicMock(returncode=0)
        run = MagicMock(return_value=result)
        monkeypatch.setattr(upd.subprocess, "run", run)
        upd.run_install("1.9.0")
        assert "runpod==1.9.0" in run.call_args[0][0]

    def test_failure_raises(self, monkeypatch):
        from unittest.mock import MagicMock

        result = MagicMock(returncode=1, stderr="resolution failed")
        monkeypatch.setattr(
            upd.subprocess, "run", MagicMock(return_value=result)
        )
        with pytest.raises(RuntimeError, match="resolution failed"):
            upd.run_install("1.9.0")


class TestUpdateNotice:
    def test_notice_printed_when_newer(self, monkeypatch, capsys):
        monkeypatch.setattr(upd, "_newer_version", "9.9.9")
        upd._check_done.set()
        upd._print_update_notice()
        err = capsys.readouterr().err
        assert "9.9.9" in err
        assert "rp update" in err

    def test_no_notice_when_current(self, monkeypatch, capsys):
        monkeypatch.setattr(upd, "_newer_version", None)
        upd._check_done.set()
        upd._print_update_notice()
        assert capsys.readouterr().err == ""

    def test_no_notice_when_check_incomplete(self, monkeypatch, capsys):
        monkeypatch.setattr(upd, "_newer_version", "9.9.9")
        monkeypatch.setattr(upd, "_check_done", threading.Event())
        upd._print_update_notice()
        assert capsys.readouterr().err == ""


class TestStartBackgroundCheck:
    def test_starts_once(self, monkeypatch):
        monkeypatch.setattr(upd, "_started", threading.Event())
        monkeypatch.setattr(upd, "_is_interactive", lambda: True)
        threads = []
        monkeypatch.setattr(
            upd.threading,
            "Thread",
            lambda **kw: threads.append(kw) or MagicMock(),
        )
        upd.start_background_check()
        upd.start_background_check()
        assert len(threads) == 1

    def test_skips_non_tty(self, monkeypatch):
        monkeypatch.setattr(upd, "_started", threading.Event())
        monkeypatch.setattr(upd, "_is_interactive", lambda: False)
        threads = []
        monkeypatch.setattr(
            upd.threading,
            "Thread",
            lambda **kw: threads.append(kw) or MagicMock(),
        )
        upd.start_background_check()
        assert threads == []
