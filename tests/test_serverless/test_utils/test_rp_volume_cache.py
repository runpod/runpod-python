import json
import os
import shutil
import subprocess
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
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    f = cache / "model.bin"
    f.write_text("v1")
    vc.sync(background=False)

    time.sleep(0.01)
    f.write_text("v2-longer")
    os.utime(f, (time.time() + 10, time.time() + 10))
    assert vc._do_sync() == 1  # one changed small file repacked

    f.unlink()
    fresh = VolumeCache([str(cache)], namespace="ep1", volume_path=str(vol))
    fresh.hydrate()
    assert f.read_text() == "v2-longer"


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

    manifest = vc._read_manifest()
    manifest_paths = [e["path"] for e in manifest["small"] + manifest["big"]]
    assert str(link) not in manifest_paths


# --------------------------------------------------------------------------- #
# hydrate destination safety
# --------------------------------------------------------------------------- #


def test_hydrate_skips_unsafe_destination(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._big_root, exist_ok=True)
    # craft a manifest whose big entry maps outside the configured dirs
    escape = str(tmp_path / "outside" / "evil.txt")
    big_src = os.path.join(vc._big_root, os.path.relpath(escape, "/"))
    os.makedirs(os.path.dirname(big_src), exist_ok=True)
    with open(big_src, "w") as fh:
        fh.write("malicious")
    vc._write_manifest([], [{"path": escape, "size": 9, "mtime": 1.0}])
    assert vc.hydrate() == 0
    assert not os.path.exists(escape)


def test_hydrate_no_manifest_is_noop(tmp_path):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    assert vc._do_hydrate() == 0


def test_hydrate_restores_big_and_small(tmp_path):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    (cache / "tiny.bin").write_bytes(b"s" * 100)
    (cache / "weights.bin").write_bytes(b"w" * (256 * 1024 + 5))
    vc.sync(background=False)
    (cache / "tiny.bin").unlink()
    (cache / "weights.bin").unlink()

    fresh = VolumeCache([str(cache)], namespace="ep1", volume_path=str(vol))
    assert fresh.hydrate() == 2
    assert (cache / "tiny.bin").read_bytes() == b"s" * 100
    assert (cache / "weights.bin").read_bytes() == b"w" * (256 * 1024 + 5)


# --------------------------------------------------------------------------- #
# context manager
# --------------------------------------------------------------------------- #


def test_context_manager_hydrates_on_enter_and_syncs_on_exit(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    (cache / "model.bin").write_text("weights")

    with vc as ctx:
        assert ctx is vc

    vcmod._join_pending_syncs()

    m = vc._read_manifest()
    assert [e["path"] for e in m["small"]] == [str(cache / "model.bin")]


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
    m = vc._read_manifest()
    assert [e["path"] for e in m["small"]] == [str(cache / "model.bin")]


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


def test_read_manifest_none_when_valid_json_not_object(tmp_path):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    with open(vc._manifest_path, "w") as fh:
        fh.write("42")
    assert vc._read_manifest() is None


def test_read_manifest_none_when_unknown_version(tmp_path):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    with open(vc._manifest_path, "w") as fh:
        json.dump({"version": vcmod._FORMAT_VERSION + 1, "small": [], "big": []}, fh)
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
    assert vc._do_sync() == 1  # only model.bin
    m = vc._read_manifest()
    packed = [e["path"] for e in m["small"]] + [e["path"] for e in m["big"]]
    assert str(cache / "model.bin") in packed
    assert str(cache / "model.bin.12345.67890.rpvc.tmp") not in packed


def test_sync_writes_manifest_and_archive_for_small(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    (cache / "tiny.bin").write_bytes(b"x" * 100)
    vc._do_sync()
    assert os.path.exists(vc._manifest_path)
    assert os.path.exists(vc._small_archive_path)
    m = vc._read_manifest()
    assert [e["path"] for e in m["small"]] == [str(cache / "tiny.bin")]


def test_sync_big_file_is_unpacked_and_incremental(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    big = cache / "weights.bin"
    big.write_bytes(b"x" * (256 * 1024 + 10))
    assert vc._do_sync() == 1
    assert os.path.exists(os.path.join(vc._big_root, os.path.relpath(str(big), "/")))
    assert vc._do_sync() == 0  # unchanged -> no recopy, no repack


def test_sync_reclassifies_small_as_big_without_tar(tmp_path, monkeypatch):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    (cache / "tiny.bin").write_bytes(b"x" * 100)
    monkeypatch.setattr(vc, "_tar_binary", lambda: None)
    monkeypatch.setattr(tarfile, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("no tar")))
    assert vc._do_sync() == 1
    m = vc._read_manifest()
    assert m["small"] == []  # nothing packed
    assert [e["path"] for e in m["big"]] == [str(cache / "tiny.bin")]  # unpacked instead
    assert not os.path.exists(vc._small_archive_path)


def test_hydrate_ignores_stale_archive_after_reclassify(tmp_path, monkeypatch):
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    tiny = cache / "tiny.bin"
    tiny.write_bytes(b"v1" * 10)

    vc.sync(background=False)
    assert os.path.exists(vc._small_archive_path)
    manifest = vc._read_manifest()
    assert [e["path"] for e in manifest["small"]] == [str(tiny)]

    # Modify the file, then force sync #2's pack to fail so it reclassifies
    # the file as big -- the stale small.tar must not survive this.
    tiny.write_bytes(b"v2-longer-content")
    monkeypatch.setattr(vc, "_tar_binary", lambda: None)
    monkeypatch.setattr(tarfile, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("no tar")))
    vc._do_sync()

    manifest = vc._read_manifest()
    assert manifest["small"] == []
    assert not os.path.exists(vc._small_archive_path)

    # Read normally (unpatched) from here on.
    tiny.unlink()
    fresh = VolumeCache([str(cache)], namespace="ep1", volume_path=str(vol))
    fresh.hydrate()

    assert tiny.read_bytes() == b"v2-longer-content"


def test_hydrate_does_not_resurrect_deleted_small_file(tmp_path):
    # A small file deleted locally while other small files remain must be
    # repacked out of small.tar; _changed_vs_manifest reports only
    # additions/modifications, so without a deletion check the stale archive
    # would resurrect the file on a later hydrate.
    vc, cache, vol = _mk_cache_with_volume(tmp_path)
    a = cache / "a.bin"
    b = cache / "b.bin"
    a.write_bytes(b"aaa")
    b.write_bytes(b"bbb")
    vc.sync(background=False)
    assert sorted(e["path"] for e in vc._read_manifest()["small"]) == sorted(
        [str(a), str(b)]
    )

    # Delete b; re-sync. a is unchanged, but the small set shrank -> repack.
    b.unlink()
    vc._do_sync()
    assert [e["path"] for e in vc._read_manifest()["small"]] == [str(a)]
    with tarfile.open(vc._small_archive_path) as tf:
        assert os.path.relpath(str(b), "/") not in tf.getnames()

    # Fresh cold worker: both gone locally. Hydrate restores a, never b.
    a.unlink()
    fresh = VolumeCache([str(cache)], namespace="ep1", volume_path=str(vol))
    fresh.hydrate()
    assert a.read_bytes() == b"aaa"
    assert not b.exists()


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


def test_pack_small_subprocess_tar_failure_leaves_no_partial(tmp_path, monkeypatch):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    (cache / "a.txt").write_text("aaa")
    # Force the subprocess-tar branch, then fail the subprocess call itself.
    monkeypatch.setattr(vc, "_tar_binary", lambda: "/usr/bin/tar")

    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, "tar")

    monkeypatch.setattr(subprocess, "run", boom)
    assert vc._pack_small([str(cache / "a.txt")]) is False
    assert not os.path.exists(vc._small_archive_path)  # atomic: no partial archive
    # No leftover .list temp file in the mirror root.
    assert not any(name.endswith(".list") for name in os.listdir(vc._mirror_root))


# --------------------------------------------------------------------------- #
# extract small
# --------------------------------------------------------------------------- #


def test_extract_small_restores_files(tmp_path, monkeypatch):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    monkeypatch.setattr(vc, "_tar_binary", lambda: None)  # deterministic pure-Python tarfile pack
    os.makedirs(vc._mirror_root, exist_ok=True)
    (cache / "a.txt").write_text("hello")
    vc._pack_small([str(cache / "a.txt")])
    (cache / "a.txt").unlink()
    assert vc._extract_small() == 1
    assert (cache / "a.txt").read_text() == "hello"


def test_extract_small_skips_unsafe_member(tmp_path, monkeypatch):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    monkeypatch.setattr(vc, "_tar_binary", lambda: None)  # deterministic pure-Python tarfile pack
    os.makedirs(vc._mirror_root, exist_ok=True)
    outside = tmp_path / "outside" / "evil.txt"
    outside.parent.mkdir()
    outside.write_text("malicious")
    vc._pack_small([str(outside)])          # archive contains an escaping path
    outside.unlink()
    assert vc._extract_small() == 0          # refused by _is_safe_dest
    assert not outside.exists()


def test_extract_small_incremental_skips_satisfied(tmp_path, monkeypatch):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    monkeypatch.setattr(vc, "_tar_binary", lambda: None)  # deterministic pure-Python tarfile pack
    os.makedirs(vc._mirror_root, exist_ok=True)
    (cache / "a.txt").write_text("hello")
    vc._pack_small([str(cache / "a.txt")])
    # local file already present and current -> nothing to extract
    assert vc._extract_small() == 0


def test_extract_small_zero_when_archive_missing(tmp_path):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    assert vc._extract_small() == 0


def test_extract_small_never_raises_when_getmembers_raises(tmp_path, monkeypatch):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    monkeypatch.setattr(vc, "_tar_binary", lambda: None)  # deterministic pure-Python tarfile pack
    os.makedirs(vc._mirror_root, exist_ok=True)
    (cache / "a.txt").write_text("hello")
    vc._pack_small([str(cache / "a.txt")])

    # tarfile.open() succeeds (archive exists and opens fine), but the inner
    # tf.getmembers() call raises tarfile.TarError. This exercises the inner
    # guard in _extract_small, distinct from the outer open()-time guard.
    class FakeTarFile:
        def getmembers(self):
            raise tarfile.TarError("corrupt member table")

        def close(self):
            pass

    monkeypatch.setattr(tarfile, "open", lambda *a, **k: FakeTarFile())
    assert vc._extract_small() == 0


def test_extract_small_skips_non_file_members(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    os.makedirs(vc._mirror_root, exist_ok=True)
    (cache / "sub").mkdir()
    (cache / "sub" / "a.txt").write_text("hello")
    # Build the archive by hand so it contains a real directory member
    # alongside the file member; _extract_small must skip the directory
    # (member.isfile() is False) without raising.
    with tarfile.open(vc._small_archive_path, "w") as tf:
        tf.add(str(cache / "sub"), arcname=_rel(str(cache / "sub")), recursive=False)
        tf.add(str(cache / "sub" / "a.txt"), arcname=_rel(str(cache / "sub" / "a.txt")))
    (cache / "sub" / "a.txt").unlink()
    assert vc._extract_small() == 1
    assert (cache / "sub" / "a.txt").read_text() == "hello"


# --------------------------------------------------------------------------- #
# copy_parallel (Task 5)
# --------------------------------------------------------------------------- #


def test_copy_parallel_copies_needed_only(tmp_path):
    vc, cache, _vol = _mk_cache_with_volume(tmp_path)
    src1 = cache / "s1"
    src1.write_text("one")
    src2 = cache / "s2"
    src2.write_text("two")
    dst1 = tmp_path / "d1"
    dst2 = tmp_path / "d2"
    # pre-satisfy dst1 so it is skipped
    shutil.copy2(str(src1), str(dst1))
    n = vc._copy_parallel([(str(src1), str(dst1)), (str(src2), str(dst2))])
    assert n == 1
    assert dst2.read_text() == "two"


def test_copy_parallel_empty_is_zero(tmp_path):
    vc, _cache, _vol = _mk_cache_with_volume(tmp_path)
    assert vc._copy_parallel([]) == 0
