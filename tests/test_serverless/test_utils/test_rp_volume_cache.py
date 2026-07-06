import os
import pytest
from runpod.serverless.utils.rp_volume_cache import VolumeCache


def test_unavailable_when_volume_dir_missing(tmp_path):
    vc = VolumeCache([str(tmp_path / "cache")], namespace="ep1",
                     volume_path=str(tmp_path / "no-volume"))
    assert vc.available is False


def test_available_when_volume_present_and_namespace_set(tmp_path):
    vol = tmp_path / "volume"
    vol.mkdir()
    vc = VolumeCache([str(tmp_path / "cache")], namespace="ep1", volume_path=str(vol))
    assert vc.available is True


def test_unavailable_without_namespace(tmp_path, monkeypatch):
    monkeypatch.delenv("RUNPOD_ENDPOINT_ID", raising=False)
    vol = tmp_path / "volume"
    vol.mkdir()
    vc = VolumeCache([str(tmp_path / "cache")], volume_path=str(vol))
    assert vc.available is False


def test_namespace_defaults_to_endpoint_id(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "endpoint-xyz")
    vol = tmp_path / "volume"
    vol.mkdir()
    vc = VolumeCache([str(tmp_path / "cache")], volume_path=str(vol))
    assert vc._shard_dir == os.path.join(str(vol), ".cache", "endpoint-xyz")
