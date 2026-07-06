import os
import tarfile
import time
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


def _mk_cache_with_volume(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    vol = tmp_path / "volume"
    vol.mkdir()
    vc = VolumeCache([str(cache)], namespace="ep1", volume_path=str(vol))
    return vc, cache, vol


def test_sync_packs_files_created_after_baseline(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    vc._baseline = time.time() - 5
    (cache / "model.bin").write_text("weights")
    assert vc.sync() is True
    shards = vc._list_shards()
    assert len(shards) == 1
    with tarfile.open(shards[0]) as tar:
        names = tar.getnames()
    assert os.path.relpath(str(cache / "model.bin"), "/") in names


def test_sync_excludes_files_older_than_baseline(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    old = cache / "old.bin"
    old.write_text("x")
    os.utime(old, (time.time() - 100, time.time() - 100))
    vc._baseline = time.time()
    assert vc.sync() is False


def test_sync_skips_excluded_paths(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    vc._baseline = time.time() - 5
    (cache / "refs").mkdir()
    (cache / "refs" / "main").write_text("ref")
    assert vc.sync() is False


def test_sync_shard_names_are_unique_per_call(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    vc._baseline = time.time() - 5
    (cache / "a.bin").write_text("a")
    vc.sync()
    (cache / "b.bin").write_text("b")
    vc.sync()
    assert len(vc._list_shards()) == 2


def test_sync_noop_when_unavailable(tmp_path):
    vc = VolumeCache([str(tmp_path / "c")], namespace="ep1",
                     volume_path=str(tmp_path / "missing"))
    assert vc.sync() is False


def test_sync_tolerates_coarse_mtime_granularity(tmp_path):
    # A fresh instance sets baseline to now - epsilon. A file whose mtime is
    # floored to the current integer second (as coarse NFS filesystems report)
    # must still be picked up, not silently dropped.
    cache = tmp_path / "cache"
    cache.mkdir()
    vol = tmp_path / "volume"
    vol.mkdir()
    vc = VolumeCache([str(cache)], namespace="ep1", volume_path=str(vol))
    f = cache / "model.bin"
    f.write_text("weights")
    now = time.time()
    os.utime(f, (float(int(now)), float(int(now))))  # floor mtime to integer second
    assert vc.sync() is True


def test_hydrate_noop_when_no_shards(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    assert vc.hydrate() is False


def test_hydrate_restores_files_to_absolute_paths(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    vc._baseline = time.time() - 5
    (cache / "model.bin").write_text("weights")
    vc.sync()
    (cache / "model.bin").unlink()          # simulate a fresh cold worker
    assert vc.hydrate() is True
    assert (cache / "model.bin").read_text() == "weights"


def test_hydrate_later_shard_overwrites_earlier(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    vc._baseline = time.time() - 5
    f = cache / "model.bin"
    f.write_text("v1")
    vc.sync()
    time.sleep(0.01)
    f.write_text("v2")
    vc._baseline = time.time() - 5
    vc.sync()
    f.unlink()
    vc._clear_marker_for_test()
    vc.hydrate()
    assert f.read_text() == "v2"


def test_hydrate_is_idempotent_via_marker(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    vc._baseline = time.time() - 5
    (cache / "model.bin").write_text("weights")
    vc.sync()
    assert vc.hydrate() is True     # first hydrate extracts
    assert vc.hydrate() is False    # marker current -> no-op


def test_rejects_member_outside_configured_dirs(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    m = tarfile.TarInfo(name="etc/passwd")   # resolves to /etc/passwd, outside cache
    m.type = tarfile.REGTYPE
    assert vc._is_safe_member(m) is False


def test_rejects_symlink_member(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    rel = os.path.relpath(str(cache / "link"), "/")
    m = tarfile.TarInfo(name=rel)
    m.type = tarfile.SYMTYPE
    m.linkname = "/etc/passwd"
    assert vc._is_safe_member(m) is False


def test_accepts_regular_member_inside_dirs(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    rel = os.path.relpath(str(cache / "model.bin"), "/")
    m = tarfile.TarInfo(name=rel)
    m.type = tarfile.REGTYPE
    assert vc._is_safe_member(m) is True


def test_rejects_sibling_prefix_collision(tmp_path):
    # An allowed dir ".../cache" must NOT match a sibling ".../cache-evil";
    # this locks the separator-anchored prefix check against substring regressions.
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    evil = tmp_path / "cache-evil"
    evil.mkdir()
    rel = os.path.relpath(str(evil / "x"), "/")
    m = tarfile.TarInfo(name=rel)
    m.type = tarfile.REGTYPE
    assert vc._is_safe_member(m) is False


def test_rejects_hardlink_member(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    rel = os.path.relpath(str(cache / "hl"), "/")
    m = tarfile.TarInfo(name=rel)
    m.type = tarfile.LNKTYPE
    m.linkname = "root/.cache/x"
    assert vc._is_safe_member(m) is False


def test_retention_prunes_oldest_shards_past_cap(tmp_path):
    cache = tmp_path / "cache"; cache.mkdir()
    vol = tmp_path / "volume"; vol.mkdir()
    cap_bytes = 1500
    vc = VolumeCache([str(cache)], namespace="ep1", volume_path=str(vol),
                     max_size_gb=cap_bytes / (1024 ** 3))
    for i in range(4):
        vc._baseline = time.time() - 5
        (cache / f"f{i}.bin").write_text("x" * 800)
        vc.sync()
        time.sleep(0.01)
    total = sum(os.path.getsize(s) for s in vc._list_shards())
    assert total <= cap_bytes


def test_no_retention_when_cap_is_none(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    for i in range(3):
        vc._baseline = time.time() - 5
        (cache / f"f{i}.bin").write_text("data")
        vc.sync()
    assert len(vc._list_shards()) == 3


def test_best_effort_swallows_and_returns_default(tmp_path, monkeypatch):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    def boom():
        raise RuntimeError("disk exploded")
    monkeypatch.setattr(vc, "_do_sync", boom)
    (cache / "x.bin").write_text("x")
    vc._baseline = time.time() - 5
    assert vc.sync() is False        # swallowed, no raise


def test_best_effort_false_reraises(tmp_path, monkeypatch):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    vc._best_effort = False
    def boom():
        raise RuntimeError("disk exploded")
    monkeypatch.setattr(vc, "_do_sync", boom)
    with pytest.raises(RuntimeError):
        vc.sync()


def test_warm_hydrates_on_enter_and_syncs_on_exit(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    calls = []
    vc.hydrate = lambda: calls.append("hydrate")
    vc.sync = lambda: calls.append("sync")
    with vc.warm():
        calls.append("body")
    assert calls == ["hydrate", "body", "sync"]


def test_warm_syncs_even_on_exception(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    calls = []
    vc.hydrate = lambda: calls.append("hydrate")
    vc.sync = lambda: calls.append("sync")
    with pytest.raises(ValueError):
        with vc.warm():
            raise ValueError("boom")
    assert calls == ["hydrate", "sync"]
