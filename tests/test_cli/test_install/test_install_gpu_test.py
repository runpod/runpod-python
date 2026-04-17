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
            "https://github.com/runpod/runpod-python/releases/download/"
            "v1.9.0/gpu_test"
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
        # 0o755 after chmod (owner rwx, group/others r-x)
        assert dest.stat().st_mode & 0o777 == 0o755

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
