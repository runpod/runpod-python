import os
import tarfile
import time
import pytest
import runpod.serverless.utils.rp_volume_cache as vcmod

VolumeCache = vcmod.VolumeCache


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


@pytest.mark.parametrize("bad_namespace", ["../evil", "a/b", "/etc", ".."])
def test_namespace_rejects_unsafe_values(tmp_path, bad_namespace):
    vol = tmp_path / "volume"
    vol.mkdir()
    with pytest.raises(ValueError):
        VolumeCache([str(tmp_path / "cache")], namespace=bad_namespace, volume_path=str(vol))


def test_namespace_accepts_normal_value(tmp_path):
    vol = tmp_path / "volume"
    vol.mkdir()
    vc = VolumeCache([str(tmp_path / "cache")], namespace="ep1", volume_path=str(vol))
    assert vc._namespace == "ep1"


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


def test_retention_tolerates_shard_removed_concurrently(tmp_path, monkeypatch):
    # A concurrent worker prunes/removes a shard between _list_shards() and the
    # getsize() lookups; the size lookup must degrade to 0 instead of raising.
    cache = tmp_path / "cache"; cache.mkdir()
    vol = tmp_path / "volume"; vol.mkdir()
    cap_bytes = 1000
    vc = VolumeCache([str(cache)], namespace="ep1", volume_path=str(vol),
                     max_size_gb=cap_bytes / (1024 ** 3))
    for i in range(3):
        vc._baseline = time.time() - 5
        (cache / f"f{i}.bin").write_text("x" * 800)
        vc.sync()
        time.sleep(0.01)

    real_getsize = os.path.getsize

    def flaky_getsize(path):
        if str(path).endswith(".tar") and "f0" not in str(path):
            raise FileNotFoundError(path)
        return real_getsize(path)

    monkeypatch.setattr(os.path, "getsize", flaky_getsize)
    # Should not raise despite getsize() failures on some shards.
    vc._enforce_retention()


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
    raised = False
    try:
        with vc.warm():
            raise ValueError("boom")
    except ValueError:
        raised = True
    assert raised
    assert calls == ["hydrate", "sync"]


def test_volumecache_exported_from_serverless():
    from runpod import serverless as sls
    from runpod.serverless import utils as sls_utils
    assert sls.VolumeCache is sls_utils.VolumeCache
    assert "VolumeCache" in sls.__all__


def test_build_default_cache_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNPOD_VOLUME_CACHE", "0")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep1")
    assert vcmod.build_default_cache() is None


def test_build_default_cache_none_when_no_volume(tmp_path, monkeypatch):
    monkeypatch.delenv("RUNPOD_VOLUME_CACHE", raising=False)
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep1")
    monkeypatch.setattr(vcmod.VolumeCache, "available", property(lambda self: False))
    assert vcmod.build_default_cache() is None


def test_discover_model_dirs_includes_hf_home_and_extras(monkeypatch):
    monkeypatch.setenv("HF_HOME", "/models/hf")
    monkeypatch.setenv("RUNPOD_CACHE_DIRS", "/a" + os.pathsep + "/b")
    dirs = vcmod._discover_model_dirs()
    assert "/models/hf" in dirs and "/a" in dirs and "/b" in dirs


def test_sync_after_job_runs_once(monkeypatch):
    vcmod.reset_builtin_state_for_test()
    counter = {"n": 0}
    fake = type("F", (), {"sync": lambda self: counter.__setitem__("n", counter["n"] + 1)})()
    joined = []
    class FakeThread:
        def __init__(self, target, daemon=None): self.target = target
        def start(self): self.target(); joined.append(True)
    monkeypatch.setattr(vcmod.threading, "Thread", FakeThread)
    vcmod.set_active_cache(fake)
    vcmod.sync_after_job()
    vcmod.sync_after_job()
    assert counter["n"] == 1


def test_run_worker_hydrates_registered_cache(monkeypatch):
    from runpod.serverless import worker

    vcmod.reset_builtin_state_for_test()
    try:
        async def _noop_fitness_checks():
            return None

        fake = type("F", (), {
            "hydrate": lambda self: fake_calls.append("h"),
            "sync": lambda self: None,
        })()
        fake_calls = []
        monkeypatch.setattr(worker, "build_default_cache", lambda: fake, raising=False)
        monkeypatch.setattr(worker.rp_scale, "JobScaler",
                            lambda config: type("J", (), {"start": lambda self: None})())
        monkeypatch.setattr(worker.heartbeat, "start_ping", lambda mirror: None)
        monkeypatch.setattr(worker, "run_fitness_checks", _noop_fitness_checks)
        worker.run_worker({"handler": lambda job: job})
        assert "h" in fake_calls
    finally:
        vcmod.reset_builtin_state_for_test()


def test_build_default_cache_survives_bad_max_gb(monkeypatch):
    vcmod.reset_builtin_state_for_test()
    monkeypatch.delenv("RUNPOD_VOLUME_CACHE", raising=False)
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep1")
    monkeypatch.setenv("RUNPOD_VOLUME_CACHE_MAX_GB", "not-a-number")
    assert vcmod.build_default_cache() is None   # degrades, does not raise


def test_two_workers_produce_independently_hydratable_shards(tmp_path):
    # Distinct worker_ids must write non-colliding shards that both hydrate (spec acceptance).
    cache = tmp_path / "cache"; cache.mkdir()
    vol = tmp_path / "volume"; vol.mkdir()
    a = VolumeCache([str(cache)], namespace="ep1", volume_path=str(vol))
    a._worker_id = "workerA"
    a._baseline = time.time() - 5
    (cache / "a.bin").write_text("aaa")
    assert a.sync() is True
    b = VolumeCache([str(cache)], namespace="ep1", volume_path=str(vol))
    b._worker_id = "workerB"
    b._baseline = time.time() - 5
    (cache / "b.bin").write_text("bbb")
    assert b.sync() is True
    (cache / "a.bin").unlink(); (cache / "b.bin").unlink()
    reader = VolumeCache([str(cache)], namespace="ep1", volume_path=str(vol))
    reader._clear_marker_for_test()
    assert reader.hydrate() is True
    assert (cache / "a.bin").read_text() == "aaa"
    assert (cache / "b.bin").read_text() == "bbb"


def test_build_default_cache_returns_instance_when_available(monkeypatch):
    vcmod.reset_builtin_state_for_test()
    monkeypatch.delenv("RUNPOD_VOLUME_CACHE", raising=False)
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep1")
    monkeypatch.setattr(vcmod.VolumeCache, "available", property(lambda self: True))
    vc = vcmod.build_default_cache()
    assert isinstance(vc, vcmod.VolumeCache)
