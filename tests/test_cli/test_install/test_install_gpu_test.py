"""
Tests for the gpu_test binary installer (runpod install-gpu-test).

Mocks urllib so tests don't touch the network. Verifies:
- Download URL is constructed from the installed runpod version
- SHA256 is verified before the binary is written
- Destination path matches _binary_helpers.get_binary_path() expectations
- Failure modes (HTTP error, checksum mismatch) raise cleanly
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from runpod.cli.groups.install.commands import install_gpu_test_cli
from runpod.cli.groups.install.functions import (
    BinaryChecksumMismatch,
    BinaryDownloadError,
    download_gpu_test_binary,
    release_asset_urls,
)


def _fake_http_response(body: bytes) -> MagicMock:
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.read.return_value = body
    response.status = 200
    return response


class TestReleaseAssetUrls:
    def test_urls_use_installed_version(self):
        urls = release_asset_urls(version="1.9.0")
        assert urls.binary == (
            "https://github.com/runpod/runpod-python/releases/download/v1.9.0/gpu_test"
        )
        assert urls.checksum == (
            "https://github.com/runpod/runpod-python/releases/download/"
            "v1.9.0/gpu_test.sha256"
        )

    def test_strips_leading_v_if_present(self):
        """Callers sometimes pass 'v1.9.0' by accident — accept either."""
        urls = release_asset_urls(version="v1.9.0")
        assert "/v1.9.0/" in urls.binary


class TestDownloadGpuTestBinary:
    def test_writes_binary_when_checksum_matches(self, tmp_path: Path):
        binary_body = b"\x7fELF fake binary payload"
        expected_sha = hashlib.sha256(binary_body).hexdigest()
        checksum_body = f"{expected_sha}  gpu_test\n".encode()
        dest = tmp_path / "gpu_test"

        def fake_urlopen(url, timeout):  # noqa: ARG001
            if url.endswith(".sha256"):
                return _fake_http_response(checksum_body)
            return _fake_http_response(binary_body)

        with patch(
            "runpod.cli.groups.install.functions.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            result = download_gpu_test_binary(version="1.9.0", dest=dest)

        assert result == dest
        assert dest.read_bytes() == binary_body
        # 0o750 after chmod (owner rwx, group r-x, others no access)
        assert dest.stat().st_mode & 0o777 == 0o750

    def test_raises_on_checksum_mismatch(self, tmp_path: Path):
        binary_body = b"real payload"
        wrong_sha = "0" * 64
        checksum_body = f"{wrong_sha}  gpu_test\n".encode()
        dest = tmp_path / "gpu_test"

        def fake_urlopen(url, timeout):  # noqa: ARG001
            if url.endswith(".sha256"):
                return _fake_http_response(checksum_body)
            return _fake_http_response(binary_body)

        with patch(
            "runpod.cli.groups.install.functions.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            with pytest.raises(BinaryChecksumMismatch):
                download_gpu_test_binary(version="1.9.0", dest=dest)

        assert not dest.exists(), "partial download must not be left on disk"

    def test_raises_on_http_error(self, tmp_path: Path):
        import urllib.error

        dest = tmp_path / "gpu_test"

        def fake_urlopen(url, timeout):  # noqa: ARG001
            raise urllib.error.HTTPError(
                url=url, code=404, msg="Not Found", hdrs=None, fp=None
            )

        with patch(
            "runpod.cli.groups.install.functions.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            with pytest.raises(BinaryDownloadError, match="404"):
                download_gpu_test_binary(version="9.9.9", dest=dest)


class TestDownloadGpuTestBinaryErrorPaths:
    """Coverage for failure modes added in response to code review."""

    def test_cleans_up_temp_file_on_replace_failure(self, tmp_path: Path):
        binary_body = b"payload"
        expected_sha = hashlib.sha256(binary_body).hexdigest()
        checksum_body = f"{expected_sha}  gpu_test\n".encode()
        dest = tmp_path / "sub" / "gpu_test"

        def fake_urlopen(url, timeout):  # noqa: ARG001
            if url.endswith(".sha256"):
                return _fake_http_response(checksum_body)
            return _fake_http_response(binary_body)

        with patch(
            "runpod.cli.groups.install.functions.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ), patch(
            "runpod.cli.groups.install.functions.os.replace",
            side_effect=OSError("simulated replace failure"),
        ):
            with pytest.raises(OSError, match="simulated replace failure"):
                download_gpu_test_binary(version="1.9.0", dest=dest)

        assert not dest.exists(), "final dest must not exist on failure"
        leftover = list(dest.parent.glob("tmp*"))
        assert leftover == [], f"temp file leaked: {leftover}"

    def test_checksum_error_includes_url_and_byte_count(self, tmp_path: Path):
        binary_body = b"payload bytes"
        wrong_sha = "0" * 64
        checksum_body = f"{wrong_sha}  gpu_test\n".encode()
        dest = tmp_path / "gpu_test"

        def fake_urlopen(url, timeout):  # noqa: ARG001
            if url.endswith(".sha256"):
                return _fake_http_response(checksum_body)
            return _fake_http_response(binary_body)

        with patch(
            "runpod.cli.groups.install.functions.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            with pytest.raises(BinaryChecksumMismatch) as exc_info:
                download_gpu_test_binary(version="1.9.0", dest=dest)

        msg = str(exc_info.value)
        assert "/v1.9.0/gpu_test" in msg, "error must cite the release URL"
        assert f"{len(binary_body)} bytes" in msg, "error must cite download size"


class TestInstallGpuTestCommand:
    def test_command_calls_download_with_resolved_dest(self, tmp_path: Path):
        """Command resolves destination via _binary_helpers and passes installed version."""
        fake_dest = tmp_path / "runpod" / "serverless" / "binaries" / "gpu_test"

        with patch(
            "runpod.cli.groups.install.commands.download_gpu_test_binary"
        ) as mock_download, patch(
            "runpod.cli.groups.install.commands._default_install_path",
            return_value=fake_dest,
        ), patch(
            "runpod.cli.groups.install.commands.get_version",
            return_value="1.9.0",
        ):
            mock_download.return_value = fake_dest
            runner = CliRunner()
            result = runner.invoke(install_gpu_test_cli, [])

        assert result.exit_code == 0, result.output
        mock_download.assert_called_once_with(version="1.9.0", dest=fake_dest)

    def test_command_honors_version_override(self, tmp_path: Path):
        fake_dest = tmp_path / "gpu_test"
        with patch(
            "runpod.cli.groups.install.commands.download_gpu_test_binary"
        ) as mock_download, patch(
            "runpod.cli.groups.install.commands._default_install_path",
            return_value=fake_dest,
        ):
            mock_download.return_value = fake_dest
            runner = CliRunner()
            result = runner.invoke(install_gpu_test_cli, ["--version", "1.8.0"])

        assert result.exit_code == 0
        mock_download.assert_called_once_with(version="1.8.0", dest=fake_dest)

    def test_command_exits_nonzero_on_download_error(self, tmp_path: Path):
        fake_dest = tmp_path / "gpu_test"
        with patch(
            "runpod.cli.groups.install.commands.download_gpu_test_binary",
            side_effect=BinaryDownloadError("HTTP 404"),
        ), patch(
            "runpod.cli.groups.install.commands._default_install_path",
            return_value=fake_dest,
        ), patch(
            "runpod.cli.groups.install.commands.get_version",
            return_value="1.9.0",
        ):
            runner = CliRunner()
            result = runner.invoke(install_gpu_test_cli, [])

        assert result.exit_code == 1
        assert "HTTP 404" in result.output

    def test_command_honors_dest_override(self, tmp_path: Path):
        """--dest should bypass _default_install_path and be forwarded verbatim."""
        custom_dest = tmp_path / "custom" / "gpu_test"

        with patch(
            "runpod.cli.groups.install.commands.download_gpu_test_binary"
        ) as mock_download, patch(
            "runpod.cli.groups.install.commands.get_version",
            return_value="1.9.0",
        ), patch(
            "runpod.cli.groups.install.commands._default_install_path"
        ) as mock_default:
            mock_download.return_value = custom_dest
            runner = CliRunner()
            result = runner.invoke(install_gpu_test_cli, ["--dest", str(custom_dest)])

        assert result.exit_code == 0, result.output
        mock_default.assert_not_called()
        mock_download.assert_called_once_with(version="1.9.0", dest=custom_dest)

    def test_command_errors_when_version_unknown(self):
        """If get_version() returns 'unknown', command exits with actionable hint."""
        with patch(
            "runpod.cli.groups.install.commands.get_version",
            return_value="unknown",
        ):
            runner = CliRunner()
            result = runner.invoke(install_gpu_test_cli, [])

        assert result.exit_code == 1
        assert "--version" in result.output
