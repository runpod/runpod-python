import os
import shutil
import tarfile
import threading
import time

import pytest

import runpod.serverless.utils.rp_volume_cache as vcmod

VolumeCache = vcmod.VolumeCache


@pytest.fixture(autouse=True)
def _reset_pending():
    vcmod._reset_pending_for_test()
    yield
    vcmod._join_pending_syncs()
    vcmod._reset_pending_for_test()


def _mk_cache_with_volume(tmp_path, namespace="ep1"):
    cache = tmp_path / "cache"
    cache.mkdir()
    vol = tmp_path / "volume"
    vol.mkdir()
    vc = VolumeCache([str(cache)], namespace=namespace, volume_path=str(vol))
    return vc, cache, vol


# --------------------------------------------------------------------------- #
# available
# --------------------------------------------------------------------------- #


def test_unavailable_when_volume_dir_missing(tmp_path):
    vc = VolumeCache(
        [str(tmp_path / "cache")], namespace="ep1", volume_path=str(tmp_path / "no-volume")
    )
    assert vc.available is False


def test_unavailable_without_namespace(tmp_path, monkeypatch):
    monkeypatch.delenv("RUNPOD_ENDPOINT_ID", raising=False)
    vol = tmp_path / "volume"
    vol.mkdir()
    vc = VolumeCache([str(tmp_path / "cache")], volume_path=str(vol))
    assert vc.available is False


def test_available_when_volume_present_and_namespace_set(tmp_path):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)
    assert vc.available is True


def test_namespace_defaults_to_endpoint_id(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "endpoint-xyz")
    vol = tmp_path / "volume"
    vol.mkdir()
    vc = VolumeCache([str(tmp_path / "cache")], volume_path=str(vol))
    assert vc._mirror_root == os.path.join(str(vol), ".cache", "endpoint-xyz")


# --------------------------------------------------------------------------- #
# namespace validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_namespace", ["../evil", "a/b", "/etc", "..", "a\\b", "."])
def test_namespace_rejects_unsafe_values(tmp_path, bad_namespace):
    vol = tmp_path / "volume"
    vol.mkdir()
    with pytest.raises(ValueError):
        VolumeCache([str(tmp_path / "cache")], namespace=bad_namespace, volume_path=str(vol))


def test_namespace_accepts_normal_value(tmp_path):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path, namespace="ep1")
    assert vc._namespace == "ep1"


# --------------------------------------------------------------------------- #
# sync -> hydrate round trip
# --------------------------------------------------------------------------- #


def test_sync_then_hydrate_round_trip(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    (cache / "model.bin").write_text("weights")

    vc.sync(background=False)
    (cache / "model.bin").unlink()

    fresh = VolumeCache([str(cache)], namespace="ep1", volume_path=str(vol))
    copied = fresh.hydrate()

    assert copied == 1
    assert (cache / "model.bin").read_text() == "weights"


def test_sync_is_idempotent_on_unchanged_file(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    (cache / "model.bin").write_text("weights")

    assert vc.sync(background=False) is None
    first_copied = vc._do_sync()
    assert first_copied == 0  # already synced above; nothing new to copy


def test_sync_recopies_modified_file(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    f = cache / "model.bin"
    f.write_text("v1")
    vc.sync(background=False)

    time.sleep(0.01)
    f.write_text("v2-longer")
    os.utime(f, (time.time() + 10, time.time() + 10))

    copied = vc._do_sync()
    assert copied == 1

    mirror_file = os.path.join(vc._mirror_root, os.path.relpath(str(f), "/"))
    with open(mirror_file) as fh:
        content = fh.read()
    assert content == "v2-longer"


def test_hydrate_skips_unchanged_after_first_hydrate(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    (cache / "model.bin").write_text("weights")
    vc.sync(background=False)
    (cache / "model.bin").unlink()

    fresh = VolumeCache([str(cache)], namespace="ep1", volume_path=str(vol))
    assert fresh.hydrate() == 1
    assert fresh.hydrate() == 0  # already up to date


def test_hydrate_does_not_overwrite_newer_container_file(tmp_path):
    # Same size, later mtime: _needs_copy must key off mtime here since size
    # alone can't distinguish the versions.
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    f = cache / "model.bin"
    f.write_text("weightsA")
    vc.sync(background=False)

    # Container file is newer than the mirror copy (same size).
    f.write_text("weightsB")
    os.utime(f, (time.time() + 100, time.time() + 100))

    fresh = VolumeCache([str(cache)], namespace="ep1", volume_path=str(vol))
    fresh.hydrate()
    assert f.read_text() == "weightsB"


# --------------------------------------------------------------------------- #
# exclusions
# --------------------------------------------------------------------------- #


def test_sync_skips_excluded_paths(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    (cache / "refs").mkdir()
    (cache / "refs" / "main").write_text("ref")
    (cache / "locked.lock").write_text("lock")

    copied = vc._do_sync()
    assert copied == 0


def test_sync_skips_symlink_source(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    real = cache / "real.bin"
    real.write_text("real-data")
    link = cache / "link.bin"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")

    copied = vc._do_sync()
    assert copied == 1  # only real.bin, not the symlink

    mirror_link = os.path.join(vc._mirror_root, os.path.relpath(str(link), "/"))
    assert not os.path.exists(mirror_link)


# --------------------------------------------------------------------------- #
# hydrate destination safety
# --------------------------------------------------------------------------- #


def test_hydrate_skips_unsafe_destination(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)

    # Craft a mirror file whose mapped destination escapes the configured dirs.
    escape_rel = os.path.relpath(str(tmp_path / "outside" / "evil.txt"), "/")
    mirror_file = os.path.join(vc._mirror_root, escape_rel)
    os.makedirs(os.path.dirname(mirror_file), exist_ok=True)
    with open(mirror_file, "w") as fh:
        fh.write("malicious")

    copied = vc.hydrate()
    assert copied == 0
    assert not (tmp_path / "outside" / "evil.txt").exists()


# --------------------------------------------------------------------------- #
# context manager
# --------------------------------------------------------------------------- #


def test_context_manager_hydrates_on_enter_and_syncs_on_exit(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    (cache / "model.bin").write_text("weights")

    with vc as ctx:
        assert ctx is vc

    vcmod._join_pending_syncs()

    mirror_file = os.path.join(vc._mirror_root, os.path.relpath(str(cache / "model.bin"), "/"))
    assert os.path.exists(mirror_file)


def test_context_manager_does_not_suppress_exceptions(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    (cache / "model.bin").write_text("weights")

    raised = False
    try:
        with vc:
            raise ValueError("boom")
    except ValueError:
        raised = True
    assert raised

    vcmod._join_pending_syncs()
    mirror_file = os.path.join(vc._mirror_root, os.path.relpath(str(cache / "model.bin"), "/"))
    assert os.path.exists(mirror_file)


# --------------------------------------------------------------------------- #
# best-effort
# --------------------------------------------------------------------------- #


def test_best_effort_swallows_sync_failure_inline(tmp_path, monkeypatch):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)

    def boom():
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(vc, "_do_sync", boom)
    vc.sync(background=False)  # must not raise


def test_best_effort_false_reraises_inline(tmp_path, monkeypatch):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)
    vc._best_effort = False

    def boom():
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(vc, "_do_sync", boom)
    with pytest.raises(RuntimeError):
        vc.sync(background=False)


def test_best_effort_swallows_sync_failure_background(tmp_path, monkeypatch):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)

    def boom():
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(vc, "_do_sync", boom)
    vc.sync(background=True)
    vcmod._join_pending_syncs()  # must not raise / must not crash the thread


# --------------------------------------------------------------------------- #
# unavailable no-ops
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# low-level helper edge cases (coverage of defensive branches)
# --------------------------------------------------------------------------- #


def test_iter_files_swallows_islink_oserror(tmp_path, monkeypatch):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    (cache / "f.bin").write_text("data")

    def flaky_islink(path):
        raise OSError("boom")

    monkeypatch.setattr(os.path, "islink", flaky_islink)
    assert list(vc._iter_files(str(cache))) == []


def test_needs_copy_false_when_src_missing(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    assert vc._needs_copy(str(cache / "missing.bin"), str(cache / "dst.bin")) is False


def test_do_sync_skips_missing_dirs(tmp_path):
    vol = tmp_path / "volume"
    vol.mkdir()
    vc = VolumeCache([str(tmp_path / "does-not-exist")], namespace="ep1", volume_path=str(vol))
    assert vc._do_sync() == 0


def test_copy_file_returns_false_and_cleans_tmp_on_failure(tmp_path, monkeypatch):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    src = cache / "f.bin"
    src.write_text("data")
    dst = cache / "dst.bin"

    def flaky_copy2(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copy2", flaky_copy2)
    assert vc._copy_file(str(src), str(dst)) is False
    assert not os.path.exists(str(dst) + ".rpvc.tmp")


def test_join_pending_syncs_swallows_join_exception():
    class FlakyThread:
        def join(self):
            raise RuntimeError("join blew up")

    vcmod._pending_syncs.append(FlakyThread())
    vcmod._join_pending_syncs()  # must not raise
    assert vcmod._pending_syncs == []


def test_hydrate_noop_when_unavailable(tmp_path):
    vc = VolumeCache(
        [str(tmp_path / "cache")], namespace="ep1", volume_path=str(tmp_path / "missing")
    )
    assert vc.hydrate() == 0


def test_sync_noop_when_unavailable(tmp_path):
    vc = VolumeCache(
        [str(tmp_path / "cache")], namespace="ep1", volume_path=str(tmp_path / "missing")
    )
    assert vc.sync(background=False) is None


# --------------------------------------------------------------------------- #
# manifest and metadata
# --------------------------------------------------------------------------- #


def test_manifest_round_trip(tmp_path):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    small = [{"path": "/a", "size": 1, "mtime": 100.0}]
    big = [{"path": "/b", "size": 999999, "mtime": 200.0}]
    vc._write_manifest(small, big)
    m = vc._read_manifest()
    assert m["version"] == 1 and m["threshold"] == 256 * 1024
    assert m["small"] == small and m["big"] == big


def test_read_manifest_none_when_absent(tmp_path):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)
    assert vc._read_manifest() is None


def test_read_manifest_none_when_corrupt(tmp_path):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    with open(vc._manifest_path, "w") as fh:
        fh.write("{not json")
    assert vc._read_manifest() is None


def test_meta_satisfied_by_local(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    f = cache / "m.bin"
    f.write_bytes(b"1234")
    st = os.stat(str(f))
    assert vc._meta_satisfied_by_local({"path": str(f), "size": 4, "mtime": st.st_mtime}) is True
    # older manifest mtime -> local (newer/equal) still satisfies
    assert vc._meta_satisfied_by_local({"path": str(f), "size": 4, "mtime": st.st_mtime - 100}) is True
    # size mismatch -> not satisfied
    assert vc._meta_satisfied_by_local({"path": str(f), "size": 5, "mtime": st.st_mtime}) is False
    # missing local -> not satisfied
    assert vc._meta_satisfied_by_local({"path": str(cache / "gone"), "size": 1, "mtime": 1.0}) is False


def test_changed_vs_manifest(tmp_path):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)
    manifest = {"small": [{"path": "/a", "size": 1, "mtime": 100.0}], "big": []}
    metas = [
        {"path": "/a", "size": 1, "mtime": 100.0},   # unchanged
        {"path": "/c", "size": 2, "mtime": 100.0},   # new
    ]
    changed = vc._changed_vs_manifest(metas, manifest, "small")
    assert [m["path"] for m in changed] == ["/c"]


# --------------------------------------------------------------------------- #
# exports
# --------------------------------------------------------------------------- #


def test_volumecache_exported_from_serverless():
    from runpod import serverless as sls
    from runpod.serverless import utils as sls_utils

    assert sls.VolumeCache is sls_utils.VolumeCache
    assert "VolumeCache" in sls.__all__


# --------------------------------------------------------------------------- #
# size partition and packed format
# --------------------------------------------------------------------------- #


def test_partition_splits_at_threshold(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    small = cache / "small.bin"
    small.write_bytes(b"x" * (256 * 1024 - 1))
    exact = cache / "exact.bin"
    exact.write_bytes(b"x" * (256 * 1024))  # >= threshold -> big
    big = cache / "big.bin"
    big.write_bytes(b"x" * (256 * 1024 + 1))
    s, b = vc._partition([str(small), str(exact), str(big)])
    assert s == [str(small)]
    assert sorted(b) == sorted([str(exact), str(big)])


def test_partition_drops_unstatable(tmp_path):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)
    s, b = vc._partition([str(tmp_path / "gone.bin")])
    assert s == [] and b == []


def test_default_max_workers_is_positive(tmp_path):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)
    assert isinstance(vc._max_workers, int) and vc._max_workers >= 1


def test_mirror_layout_paths(tmp_path):
    vc, _cache, vol = _mk_cache_with_volume(tmp_path)
    assert vc._manifest_path == os.path.join(vc._mirror_root, "manifest.json")
    assert vc._small_archive_path == os.path.join(vc._mirror_root, "small.tar")
    assert vc._big_root == os.path.join(vc._mirror_root, "big")


# --------------------------------------------------------------------------- #
# v2 review fixes
# --------------------------------------------------------------------------- #


def test_iter_files_skips_inflight_temp_files(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    (cache / "model.bin").write_text("real")
    (cache / "model.bin.12345.67890.rpvc.tmp").write_text("partial")
    copied = vc._do_sync()
    assert copied == 1  # only model.bin, not the .rpvc.tmp file
    tmp_mirror = os.path.join(
        vc._mirror_root, os.path.relpath(str(cache / "model.bin.12345.67890.rpvc.tmp"), "/")
    )
    assert not os.path.exists(tmp_mirror)


def test_join_pending_syncs_bounded_by_timeout(monkeypatch):
    vcmod._reset_pending_for_test()
    monkeypatch.setattr(vcmod, "_JOIN_TIMEOUT_SECONDS", 0.05)
    started = threading.Event()
    release = threading.Event()

    def slow():
        started.set()
        release.wait(5)

    t = threading.Thread(target=slow, daemon=True)
    vcmod._register_pending(t)
    t.start()
    started.wait(1)
    # Must return promptly despite the thread still running (bounded join).
    vcmod._join_pending_syncs()
    release.set()
    vcmod._reset_pending_for_test()


# --------------------------------------------------------------------------- #
# pack small
# --------------------------------------------------------------------------- #


def _rel(p):
    return os.path.relpath(p, "/")


def test_pack_small_with_binary_round_trips(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    (cache / "a.txt").write_text("aaa")
    (cache / "sub").mkdir()
    (cache / "sub" / "b.txt").write_text("bbb")
    files = [str(cache / "a.txt"), str(cache / "sub" / "b.txt")]
    assert vc._pack_small(files) is True
    with tarfile.open(vc._small_archive_path) as tf:
        names = set(tf.getnames())
    assert _rel(files[0]) in names and _rel(files[1]) in names


def test_pack_small_falls_back_to_tarfile(tmp_path, monkeypatch):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    (cache / "a.txt").write_text("aaa")
    monkeypatch.setattr(vc, "_tar_binary", lambda: None)
    assert vc._pack_small([str(cache / "a.txt")]) is True
    with tarfile.open(vc._small_archive_path) as tf:
        assert _rel(str(cache / "a.txt")) in tf.getnames()


def test_pack_small_atomic_no_partial_on_failure(tmp_path, monkeypatch):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    (cache / "a.txt").write_text("aaa")
    monkeypatch.setattr(vc, "_tar_binary", lambda: None)

    def boom(*a, **k):
        raise OSError("no space")

    monkeypatch.setattr(tarfile, "open", boom)
    assert vc._pack_small([str(cache / "a.txt")]) is False
    assert not os.path.exists(vc._small_archive_path)  # atomic: no partial archive


# --------------------------------------------------------------------------- #
# extract small
# --------------------------------------------------------------------------- #


def test_extract_small_restores_files(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    (cache / "a.txt").write_text("hello")
    vc._pack_small([str(cache / "a.txt")])
    (cache / "a.txt").unlink()
    assert vc._extract_small() == 1
    assert (cache / "a.txt").read_text() == "hello"


def test_extract_small_skips_unsafe_member(tmp_path, monkeypatch):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    outside = tmp_path / "outside" / "evil.txt"
    outside.parent.mkdir()
    outside.write_text("malicious")
    vc._pack_small([str(outside)])          # archive contains an escaping path
    outside.unlink()
    assert vc._extract_small() == 0          # refused by _is_safe_dest
    assert not outside.exists()


def test_extract_small_incremental_skips_satisfied(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    (cache / "a.txt").write_text("hello")
    vc._pack_small([str(cache / "a.txt")])
    # local file already present and current -> nothing to extract
    assert vc._extract_small() == 0


def test_extract_small_zero_when_archive_missing(tmp_path):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    assert vc._extract_small() == 0
