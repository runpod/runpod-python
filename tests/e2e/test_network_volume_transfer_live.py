"""live round trip through a network volume's s3 api.

creates a 10 GB volume (billed while it exists) and deletes it afterwards, so
it only runs when asked for: set RUNPOD_E2E_VOLUMES=1 together with
RUNPOD_API_KEY, RUNPOD_S3_ACCESS_KEY and RUNPOD_S3_SECRET_KEY.
"""

import hashlib
import os
import uuid

import pytest

import runpod
from runpod.api.rest import run_rest_request

DATACENTER = os.environ.get("RUNPOD_E2E_VOLUME_DATACENTER", "EU-RO-1")
REQUIRED = ("RUNPOD_API_KEY", "RUNPOD_S3_ACCESS_KEY", "RUNPOD_S3_SECRET_KEY")

pytestmark = pytest.mark.skipif(
    os.environ.get("RUNPOD_E2E_VOLUMES") != "1"
    or not all(os.environ.get(name) for name in REQUIRED),
    reason="set RUNPOD_E2E_VOLUMES=1 and " + ", ".join(REQUIRED),
)


@pytest.fixture
def volume():
    created = run_rest_request(
        "POST",
        "/v2/network-volumes",
        json={
            "name": f"e2e-transfer-{uuid.uuid4().hex[:8]}",
            "size": 10,
            "dataCenter": DATACENTER,
        },
    )
    try:
        yield runpod.NetworkVolume(created["id"], create=False)
    finally:
        run_rest_request("DELETE", f"/v2/network-volumes/{created['id']}")


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def test_round_trip_then_rerun_skips(volume, tmp_path):
    big = tmp_path / "up" / "big.bin"
    big.parent.mkdir()
    # over the 64 MiB single-request limit, so the upload is multipart and the
    # download is chunked
    big.write_bytes(os.urandom(70 * 1024 * 1024))
    folder = tmp_path / "up" / "folder"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.txt").write_text("a")
    (folder / "sub" / "b.txt").write_text("b")

    assert volume.upload(big, "e2e/big.bin")["files_transferred"] == 1
    assert volume.upload(folder, "e2e/folder")["files_transferred"] == 2

    down = tmp_path / "down"
    volume.download("e2e/big.bin", down / "big.bin")
    volume.download("e2e/folder/", str(down / "folder"))
    assert _sha256(down / "big.bin") == _sha256(big)
    assert (down / "folder" / "sub" / "b.txt").read_text() == "b"

    # a rerun finds everything unchanged in both directions
    assert volume.upload(folder, "e2e/folder")["files_skipped"] == 2
    assert volume.download("e2e/folder/", str(down / "folder"))["files_skipped"] == 2
