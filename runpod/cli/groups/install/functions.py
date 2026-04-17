"""
Download and install the optional gpu_test binary from a GitHub release.

The binary is NOT bundled in PyPI wheels to keep them universal
(py3-none-any). Runpod GPU workers that want the native CUDA memory
allocation test can fetch it from the GitHub release matching their
installed runpod version.

See docs/serverless/gpu_binary_compilation.md for usage.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GITHUB_REPO = "runpod/runpod-python"
DOWNLOAD_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class ReleaseAssetUrls:
    binary: str
    checksum: str


class BinaryDownloadError(RuntimeError):
    """Raised when the binary or checksum cannot be fetched."""


class BinaryChecksumMismatch(RuntimeError):
    """Raised when the downloaded binary's sha256 does not match the expected value."""


def release_asset_urls(version: str) -> ReleaseAssetUrls:
    """Build release-asset URLs for a given runpod version.

    Accepts either '1.9.0' or 'v1.9.0' — the leading 'v' is optional.
    """
    clean = version.lstrip("v")
    base = (
        f"https://github.com/{GITHUB_REPO}/releases/download/v{clean}/gpu_test"
    )
    return ReleaseAssetUrls(binary=base, checksum=f"{base}.sha256")


def _fetch(url: str) -> bytes:
    try:
        with urllib.request.urlopen(
            url, timeout=DOWNLOAD_TIMEOUT_SECONDS
        ) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise BinaryDownloadError(
            f"HTTP {exc.code} fetching {url}: {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise BinaryDownloadError(
            f"Network error fetching {url}: {exc.reason!r}"
        ) from exc


def _parse_sha256(checksum_body: bytes) -> str:
    """Extract the hex digest from a 'sha256  filename' line."""
    text = checksum_body.decode("utf-8", errors="replace").strip()
    first_token = text.split()[0] if text else ""
    if len(first_token) != 64:
        raise BinaryDownloadError(
            f"checksum file did not contain a sha256 digest: {text!r}"
        )
    return first_token.lower()


def download_gpu_test_binary(version: str, dest: Path) -> Path:
    """Download gpu_test from the matching GitHub release and install it at dest.

    Verifies sha256 before writing to the final destination. On checksum
    mismatch or HTTP failure, no partial file is left at dest.

    Returns the destination path on success.
    """
    urls = release_asset_urls(version)

    checksum_body = _fetch(urls.checksum)
    expected_sha = _parse_sha256(checksum_body)

    binary_body = _fetch(urls.binary)
    actual_sha = hashlib.sha256(binary_body).hexdigest()
    if actual_sha != expected_sha:
        raise BinaryChecksumMismatch(
            f"sha256 mismatch for {urls.binary} "
            f"({len(binary_body)} bytes): "
            f"expected {expected_sha}, got {actual_sha}"
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=dest.parent, delete=False
    ) as tmp:
        tmp.write(binary_body)
        tmp_path = Path(tmp.name)

    try:
        os.chmod(tmp_path, 0o755)
        os.replace(tmp_path, dest)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
    return dest
