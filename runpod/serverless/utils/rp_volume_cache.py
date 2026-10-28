"""Directory-mirror warm cache between local directories and a network volume.

``VolumeCache`` keeps a browsable mirror of one or more local directories on a
mounted network volume, and reconciles the two directions on demand:

- ``hydrate()``: copy files that are missing or newer on the volume mirror
  into the container (used on cold start, before the cache is populated).
- ``sync()``: copy files that are missing or newer in the container onto the
  volume mirror (used after the cache has been populated/updated).

Transport adapts to the tree's shape: files below 256 KiB are packed into a
single ``small.tar`` on the volume (collapsing per-file metadata round-trips),
larger files are copied unpacked into ``big/`` across a thread pool, and a
versioned ``manifest.json`` (written last) records the layout. Hydrate restores
large files with concurrent range reads instead of whole-file copies, which is
what object-store mounts such as Global Store need to go faster than one
sequential stream. Large-file transfers stay incremental (size/mtime diff); the
small-file archive is
re-packed whole when any small file changes. Packing uses the ``tar`` binary
when present (else stdlib ``tarfile``); extraction always uses ``tarfile``,
validating every member against the configured dirs. Best-effort: any failure
degrades to a cold worker and never raises unless ``best_effort=False``.
"""

import atexit
import collections
import errno
import json
import os
import random
import shutil
import subprocess
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from runpod.serverless.modules.rp_logger import RunPodLogger

log = RunPodLogger()

_MTIME_TOLERANCE = 2.0  # seconds; tolerate coarse (NFS) mtime granularity
_JOIN_TIMEOUT_SECONDS = 30.0  # bound the atexit wait so stalled volume I/O can't hang shutdown
_SMALL_FILE_THRESHOLD = 256 * 1024  # bytes; <-threshold packs into one tar (metadata collapse)
_FORMAT_VERSION = 1
_MANIFEST_NAME = "manifest.json"
_SMALL_ARCHIVE_NAME = "small.tar"
_BIG_SUBDIR = "big"
# Hydrate copies big files in ranges. Bytes in flight (workers x chunk, 512 MiB
# here) is what matters: on Global Store's geesefs mount, 64 x 8 MiB reaches the
# host NIC (~1.1 GiB/s), and much past ~1 GiB in flight fails reads with ENOMEM.
_HYDRATE_WORKERS = 64
_HYDRATE_CHUNK_SIZE = 8 * 1024 * 1024
# Transient errors an object-store FUSE mount returns under load; retried with
# jittered exponential backoff (up to ~6s in total) before the file is given up
# on. Jitter keeps ranges that failed together from retrying in lockstep.
_RANGE_RETRY_ERRNOS = frozenset({errno.ENOMEM, errno.EIO, errno.EAGAIN})
_RANGE_MAX_ATTEMPTS = 8
_RANGE_RETRY_BASE_SECONDS = 0.05

_RangedFile = collections.namedtuple("_RangedFile", "src dst tmp size mtime identity")


class VolumeCache:
    """Warm-cache local directories across serverless workers via a network volume.

    Maintains a mirror of ``dirs`` at ``{volume_path}/.cache/{namespace}``.
    ``hydrate()`` reconciles volume -> container; ``sync()`` reconciles
    container -> volume. Used as a context manager, the object is itself the
    "warm cache" closure: hydrate on enter, sync (in the background by
    default) on exit.

    Requires a network volume mounted at ``volume_path`` (default
    ``/runpod-volume``) and a non-empty ``namespace`` (default
    ``RUNPOD_ENDPOINT_ID``); otherwise ``available`` is False and all
    operations are no-ops.

    Args:
        dirs: Local directories to cache (e.g. a model cache like ``HF_HOME``).
        namespace: Isolation key for the on-volume mirror. Must be a single
            safe path component. Defaults to ``RUNPOD_ENDPOINT_ID``.
        volume_path: Network-volume mount point. Defaults to ``/runpod-volume``.
        best_effort: When True (default), swallow and log errors instead of
            raising.
        max_workers: Thread count for parallel copy of large files on sync.
            Defaults to ``min(32, (os.cpu_count() or 4) * 4)`` (I/O-bound, so
            oversubscribing the CPU count is fine).
        hydrate_workers: Concurrent range reads on hydrate. Defaults to
            ``max_workers`` when that is set, else 64.
        hydrate_chunk_size: Bytes per hydrate range. Defaults to 8 MiB. Peak
            hydrate memory is about ``hydrate_workers * hydrate_chunk_size``.

    Example:
        >>> with VolumeCache(dirs=["/root/.cache/huggingface"]):
        ...     model = load_model()  # downloads land in the cached dir
    """

    _EXCLUDE_SUBSTRINGS = (os.sep + "refs" + os.sep, os.sep + ".no_exist" + os.sep)

    def __init__(
        self,
        dirs,
        *,
        namespace=None,
        volume_path="/runpod-volume",
        best_effort=True,
        max_workers=None,
        hydrate_workers=None,
        hydrate_chunk_size=None,
    ):
        if isinstance(dirs, (str, bytes, os.PathLike)):
            # A bare path is one directory, not an iterable of path characters:
            # `dirs="/root/.cache"` must not be split into "/", "r", "o", ...
            dirs = [dirs]
        # Normalize to text paths: a bytes entry (e.g. dirs=b"...") would keep
        # os.walk yielding bytes names and make _is_safe_dest compare bytes
        # against str prefixes, raising TypeError that best-effort mode swallows.
        self._dirs = [os.path.realpath(os.fsdecode(os.fspath(d))) for d in dirs]
        self._namespace = namespace or os.environ.get("RUNPOD_ENDPOINT_ID") or ""
        if self._namespace and (
            os.path.isabs(self._namespace)
            or os.sep in self._namespace
            or "/" in self._namespace
            or "\\" in self._namespace
            or self._namespace in (".", "..")
        ):
            raise ValueError(
                f"namespace must be a single safe path component, got {self._namespace!r}"
            )
        # Decode to text like _dirs above: a bytes volume_path would make
        # _mirror_root's os.path.join mix bytes with the str ".cache" component
        # and raise TypeError that best-effort mode swallows, silently disabling
        # the cache.
        self._volume_path = os.fsdecode(os.fspath(volume_path))
        self._best_effort = best_effort
        self._max_workers = _positive_or_default(
            "max_workers", max_workers, min(32, (os.cpu_count() or 4) * 4)
        )
        # An explicit max_workers sized hydrate too before ranged hydrate; keep
        # honoring it so a caller who set it low doesn't silently get 64 threads.
        self._hydrate_workers = _positive_or_default(
            "hydrate_workers", hydrate_workers, max_workers or _HYDRATE_WORKERS
        )
        self._hydrate_chunk_size = _positive_or_default(
            "hydrate_chunk_size", hydrate_chunk_size, _HYDRATE_CHUNK_SIZE
        )

    @property
    def _mirror_root(self):
        return os.path.join(self._volume_path, ".cache", self._namespace)

    @property
    def _manifest_path(self):
        return os.path.join(self._mirror_root, _MANIFEST_NAME)

    @property
    def _small_archive_path(self):
        return os.path.join(self._mirror_root, _SMALL_ARCHIVE_NAME)

    @property
    def _big_root(self):
        return os.path.join(self._mirror_root, _BIG_SUBDIR)

    @property
    def available(self):
        """True iff volume_path is a mounted dir AND namespace is non-empty."""
        return bool(self._namespace) and os.path.isdir(self._volume_path)

    # ----------------------------------------------------------------- #
    # reconcile helpers
    # ----------------------------------------------------------------- #

    def _iter_files(self, root):
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(dirpath, name)
                if name.endswith(".lock") or name.endswith(".rpvc.tmp"):
                    continue
                if any(sub in path for sub in self._EXCLUDE_SUBSTRINGS):
                    continue
                try:
                    if os.path.islink(path):
                        continue
                except OSError:
                    continue
                yield path

    def _partition(self, paths):
        small, big = [], []
        for p in paths:
            try:
                size = os.stat(p).st_size
            except OSError:
                continue
            (small if size < _SMALL_FILE_THRESHOLD else big).append(p)
        return small, big

    def _file_meta(self, path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        return {"path": path, "size": st.st_size, "mtime": st.st_mtime}

    def _write_manifest(self, small_meta, big_meta):
        os.makedirs(self._mirror_root, exist_ok=True)
        payload = {
            "version": _FORMAT_VERSION,
            "threshold": _SMALL_FILE_THRESHOLD,
            "small": small_meta,
            "big": big_meta,
        }
        tmp = _tmp_path(self._manifest_path)
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, self._manifest_path)

    def _read_manifest(self):
        try:
            with open(self._manifest_path) as fh:
                obj = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(obj, dict) or obj.get("version") != _FORMAT_VERSION:
            # Not a dict, or an unknown/future format version: treat as absent
            # so callers self-heal (sync repacks and rewrites the manifest)
            # instead of raising downstream on unexpected shapes.
            return None
        return obj

    def _meta_satisfied_by_local(self, meta):
        try:
            st = os.stat(meta["path"])
        except OSError:
            return False
        return st.st_size == meta["size"] and st.st_mtime >= meta["mtime"] - _MTIME_TOLERANCE

    def _changed_vs_manifest(self, metas, manifest, key):
        prior = {e["path"]: e for e in (manifest.get(key, []) if manifest else [])}
        changed = []
        for m in metas:
            p = prior.get(m["path"])
            if p is None or p["size"] != m["size"] or m["mtime"] > p["mtime"] + _MTIME_TOLERANCE:
                changed.append(m)
        return changed

    @staticmethod
    def _needs_copy(src_path, dst_path):
        try:
            s = os.stat(src_path)
        except OSError:
            return False
        try:
            d = os.stat(dst_path)
        except OSError:
            return True
        return s.st_size != d.st_size or s.st_mtime > d.st_mtime + _MTIME_TOLERANCE

    @staticmethod
    def _sync_needs_copy(src_path, dst_path, prior):
        """Container -> mirror: does dst need a fresh copy of src?

        ``prior`` is src's entry in the previous manifest. It records the
        source metadata the mirror copy was made from, which stays reliable
        when the volume can't carry mtimes (the mirror then holds copy-time
        mtimes). Without a prior entry, only an exact size+mtime match counts
        as current.
        """
        try:
            s = os.stat(src_path)
        except OSError:
            return False
        try:
            d = os.stat(dst_path)
        except OSError:
            return True
        if s.st_size != d.st_size:
            return True
        if prior is None:
            return abs(s.st_mtime - d.st_mtime) > _MTIME_TOLERANCE
        # Only recopy when the source is NEWER than the metadata the mirror was
        # made from. A source older than the manifest entry is a stale worker or
        # a sibling that downloaded identical bytes at a different time; recopying
        # there churns every big file across workers and can overwrite newer
        # mirror bytes with older ones.
        return prior["size"] != s.st_size or s.st_mtime > prior["mtime"] + _MTIME_TOLERANCE

    def _is_safe_dest(self, dst_abs):
        target = os.path.realpath(dst_abs)
        return any(target == d or target.startswith(d + os.sep) for d in self._dirs)

    def _copy_file(self, src, dst):
        tmp = _tmp_path(dst)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, tmp)
            _copy_metadata(src, tmp)
            os.replace(tmp, dst)  # atomic; last-writer-wins under concurrency
            return True
        except OSError as exc:
            log.warn(f"VolumeCache: failed to copy {src} -> {dst}: {exc}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                # best-effort cleanup; a leftover temp file is overwritten next sync
                pass
            return False

    def _copy_parallel(self, pairs, needs_copy=None):
        """Copy every (src, dst) that needs it, in parallel.

        ``needs_copy(src, dst)`` decides which pairs to copy (default: compare
        the two files).

        Returns ``(copied, current)`` sets of source paths:
        - ``copied``  -- sources transferred successfully this call.
        - ``current`` -- sources skipped because the destination was already
          up to date.
        Their union is the set of sources the destination now holds a current
        copy of; a source in neither failed to copy. Callers that record a
        "mirror complete" manifest MUST include only sources in that union, or
        a failed copy would be marked present and later served stale. copyfile +
        atomic os.replace means a source in ``copied`` matches its destination
        at that instant, so no re-stat is needed to confirm it.
        """
        needs_copy = needs_copy or self._needs_copy
        todo = [(s, d) for s, d in pairs if needs_copy(s, d)]
        current = {s for s, _ in pairs} - {s for s, _ in todo}
        if not todo:
            return set(), current
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            # pool.map preserves input order, so zip pairs each result with its
            # source. This holds one path per copied file (unavoidable: per-file
            # identity is required to keep failed copies out of the manifest).
            results = pool.map(lambda sd: self._copy_file(sd[0], sd[1]), todo)
            copied = {s for (s, _), ok in zip(todo, results) if ok}
        return copied, current

    def _hydrate_ranged(self, entries):
        """Restore ``(src, dst, manifest_entry)`` triples with concurrent range reads.

        Every file is split into ``hydrate_chunk_size`` ranges, and one pool of
        ``hydrate_workers`` threads copies the ranges of all files, each range on
        its own descriptors. An object-store mount serves one sequential stream
        at ~140 MiB/s however many whole files are read at once, but independent
        range reads scale to the host NIC. Each source's size and mtime are pinned
        (and checked against the manifest) before its first range, and every
        range rejects a source that a concurrent sync() visibly replaced since.
        A file is written to a temp file and
        renamed into place only once every range copied, stamped with the
        manifest's source mtime, so a failure leaves the local file untouched.

        Returns the set of sources restored.
        """
        # Temp paths are fixed before any is created so an interrupt (e.g.
        # Ctrl-C) in preparation, copying or finalization removes every one.
        # Temps are pre-sized to the full file and named per pid, so a later
        # run never reuses them; leaking them can fill the disk. Removing an
        # already published temp is a no-op.
        tmps = [_tmp_path(dst) for _src, dst, _entry in entries]
        try:
            prepared = (self._prepare_ranged(*e, tmp) for e, tmp in zip(entries, tmps))
            files = [f for f in prepared if f]
            failed = self._copy_ranges(files)
            return {f.src for i, f in enumerate(files) if self._finish_ranged(f, i in failed)}
        except BaseException:
            for tmp in tmps:
                _silent_remove(tmp)
            raise

    def _prepare_ranged(self, src, dst, entry, tmp):
        try:
            st = os.stat(src)
            if st.st_size != entry["size"]:
                # A sync replaced the mirror file after the manifest was read;
                # copying entry["size"] bytes of it would publish a truncated file.
                raise OSError(f"mirror size {st.st_size} != manifest size {entry['size']}")
            identity = _file_identity(st)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(tmp, "wb") as fh:
                fh.truncate(entry["size"])
        except OSError as exc:
            log.warn(f"VolumeCache: failed to hydrate {src} -> {dst}: {exc}")
            _silent_remove(tmp)
            return None
        return _RangedFile(src, dst, tmp, entry["size"], entry["mtime"], identity)

    def _copy_ranges(self, files):
        """Copy every range of ``files`` into their temp files; return indexes that failed."""
        failed = set()

        def run(item):
            off, i, length = item
            if i in failed:
                return
            f = files[i]
            try:
                _with_range_retry(_copy_range, f.src, f.tmp, off, length, f.identity)
            except OSError as exc:
                failed.add(i)
                log.warn(f"VolumeCache: failed to hydrate {f.src} at offset {off}: {exc}")

        _run_bounded(_iter_ranges(files, self._hydrate_chunk_size), run, self._hydrate_workers)
        return failed

    @staticmethod
    def _finish_ranged(f, failed):
        if failed:
            _silent_remove(f.tmp)
            return False
        try:
            _copy_metadata(f.src, f.tmp, f.mtime)
            os.replace(f.tmp, f.dst)
            return True
        except OSError as exc:
            log.warn(f"VolumeCache: failed to hydrate {f.src} -> {f.dst}: {exc}")
            _silent_remove(f.tmp)
            return False

    @staticmethod
    def _within(base, path):
        """True iff ``path`` resolves inside ``base`` (symlinks resolved)."""
        base_real = os.path.realpath(base)
        path_real = os.path.realpath(path)
        return path_real == base_real or path_real.startswith(base_real + os.sep)

    def _guard(self, fn, default):
        try:
            return fn()
        except Exception as exc:  # best-effort: never break the worker
            if not self._best_effort:
                raise
            log.warn(f"VolumeCache operation failed: {exc}")
            return default

    def _tar_binary(self):
        return shutil.which("tar")

    def _pack_small(self, files):
        os.makedirs(self._mirror_root, exist_ok=True)
        tmp = _tmp_path(self._small_archive_path)
        rels = [os.path.relpath(f, "/") for f in files]
        try:
            if self._tar_binary():
                listing = f"{tmp}.list"
                try:
                    with open(listing, "w") as fh:
                        fh.write("\n".join(rels))
                    subprocess.run(
                        ["tar", "-C", "/", "-c", "--no-recursion", "-f", tmp, "-T", listing],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        # Suppress BSD tar AppleDouble (._*) sidecars; no-op for GNU tar.
                        env={**os.environ, "COPYFILE_DISABLE": "1"},
                    )
                finally:
                    _silent_remove(listing)
            else:
                with tarfile.open(tmp, "w") as tf:
                    for f, rel in zip(files, rels):
                        tf.add(f, arcname=rel, recursive=False)
            os.replace(tmp, self._small_archive_path)
            return True
        except (OSError, subprocess.SubprocessError, tarfile.TarError) as exc:
            log.debug(f"VolumeCache: pack small bucket failed: {exc}")
            _silent_remove(tmp)
            return False

    def _extract_small(self):
        try:
            tf = tarfile.open(self._small_archive_path)
        except (OSError, tarfile.TarError):
            return 0
        extracted = 0
        try:
            try:
                members = tf.getmembers()
            except (OSError, tarfile.TarError) as exc:
                log.debug(f"VolumeCache: corrupt archive {self._small_archive_path}: {exc}")
                return extracted
            for member in members:
                # Skip non-files (directories, symlinks, etc.)
                if not member.isfile():
                    continue
                dst = os.path.join("/", member.name)
                # Verify the destination is safe (within configured directories)
                if not self._is_safe_dest(dst):
                    continue
                # Check if the local file already satisfies this archive entry
                meta = {"path": dst, "size": member.size, "mtime": member.mtime}
                if self._meta_satisfied_by_local(meta):
                    continue
                # Extract this member
                try:
                    self._extract_member(tf, member)
                    extracted += 1
                except (OSError, tarfile.TarError) as exc:
                    log.debug(f"VolumeCache: skip extract {member.name}: {exc}")
        finally:
            tf.close()
        if extracted:
            log.info(f"VolumeCache: extracted {extracted} small file(s)")
        return extracted

    @staticmethod
    def _extract_member(tf, member):
        # Extract to "/" using the data filter when available (3.12+ path-traversal
        # hardening); the per-member _is_safe_dest check above is the portable guard.
        if hasattr(tarfile, "data_filter"):
            tf.extract(member, "/", filter="data")
        else:
            tf.extract(member, "/")

    # ----------------------------------------------------------------- #
    # hydrate / sync
    # ----------------------------------------------------------------- #

    def hydrate(self):
        """Reconcile volume mirror -> container.

        Copy files missing or newer in the container. Returns the number of
        files copied. No-op (0) if unavailable.
        """
        if not self.available:
            return 0
        return self._guard(self._do_hydrate, 0)

    def _do_hydrate(self):
        manifest = self._read_manifest()
        if manifest is None:
            return 0
        restored = 0
        if manifest.get("small"):
            restored += self._extract_small()
        todo = []
        for entry in manifest.get("big", []):
            rel = os.path.relpath(entry["path"], "/")
            dst = os.path.join("/", rel)
            src = os.path.join(self._big_root, rel)
            # Guard both ends against a crafted manifest: the destination must
            # stay inside a configured dir, and the source must resolve inside
            # big/ so a malicious entry can't read outside the mirror. Compare
            # the container against the manifest's source metadata, not the
            # mirror file's own mtime.
            if (
                self._is_safe_dest(dst)
                and self._within(self._big_root, src)
                and os.path.isfile(src)
                and not self._meta_satisfied_by_local(entry)
            ):
                todo.append((src, dst, entry))
        restored += len(self._hydrate_ranged(todo))
        if restored:
            log.info(f"VolumeCache: hydrated {restored} file(s) from {self._mirror_root}")
        return restored

    def sync(self, *, background=True):
        """Reconcile container -> volume mirror.

        Copy files missing or newer on the volume. When ``background=True``
        (default), run on a daemon thread and return immediately; a
        process-exit hook joins outstanding syncs so short-lived processes
        still complete. When False, run inline and return when done.
        """
        if not self.available:
            return
        if background:
            t = threading.Thread(target=lambda: self._guard(self._do_sync, 0), daemon=True)
            _register_pending(t)
            t.start()
        else:
            self._guard(self._do_sync, 0)

    def _do_sync(self):
        all_files = []
        for root in self._dirs:
            if os.path.isdir(root):
                all_files.extend(self._iter_files(root))
        small_files, big_files = self._partition(all_files)

        manifest = self._read_manifest()
        small_meta = [m for m in (self._file_meta(f) for f in small_files) if m]
        small_transferred = 0
        if small_meta:
            changed = self._changed_vs_manifest(small_meta, manifest, "small")
            prior_small = {e["path"] for e in (manifest.get("small", []) if manifest else [])}
            deleted = bool(prior_small - {m["path"] for m in small_meta})
            if changed or deleted:
                # Repack on any small-set change, including deletions. small.tar
                # is extracted member-by-member on hydrate, so a stale archive
                # that still holds a locally-deleted file would resurrect it;
                # _changed_vs_manifest only reports additions/modifications, so
                # deletions are detected separately here.
                if self._pack_small([m["path"] for m in small_meta]):
                    small_transferred = len(changed)
                else:
                    # pack failed (no tar binary and tarfile unusable): reclassify
                    # the small files as big and copy them unpacked instead.
                    big_files = big_files + [m["path"] for m in small_meta]
                    small_meta = []
            # else: unchanged -> the existing archive is still current
        if not small_meta:
            # No small files (or pack failed and reclassified them as big):
            # remove any stale archive before the manifest is written so a
            # crash can't leave manifest.small=[] with a live archive.
            _silent_remove(self._small_archive_path)

        big_pairs = [(f, os.path.join(self._big_root, os.path.relpath(f, "/"))) for f in big_files]
        prior_big = {e["path"]: e for e in (manifest.get("big", []) if manifest else [])}
        copied, current = self._copy_parallel(
            big_pairs,
            needs_copy=lambda src, dst: self._sync_needs_copy(src, dst, prior_big.get(src)),
        )
        # Record only sources the volume actually holds a current copy of.
        # A big file whose copy failed is in neither set, so it is omitted and
        # the manifest never claims it present -- hydrate then skips it rather
        # than restoring stale bytes left by the failed overwrite.
        mirrored = copied | current
        big_meta = [
            m for m in (self._file_meta(f) for f in big_files) if m and m["path"] in mirrored
        ]
        big_copied = len(copied)

        self._write_manifest(small_meta, big_meta)
        total = big_copied + small_transferred
        if total:
            log.info(f"VolumeCache: synced {total} file(s) to {self._mirror_root}")
        return total

    # ----------------------------------------------------------------- #
    # context manager
    # ----------------------------------------------------------------- #

    def __enter__(self):
        self.hydrate()
        return self

    def __exit__(self, *exc):
        self.sync(background=True)
        return None


# ----------------------------------------------------------------------- #
# utilities
# ----------------------------------------------------------------------- #


def _copy_metadata(src, dst, mtime=None):
    """Stamp dst with an mtime and src's permission bits, each best-effort.

    The mtime is ``mtime`` when given (hydrate passes the manifest's source
    mtime), else src's own. Some volume filesystems (FUSE object stores such
    as Global Store) reject utime and chmod with EPERM. The copy is still
    valid then: reconciliation compares against the manifest's recorded
    source metadata, not the mirror file's copy-time mtime. Only the
    permission bits are lost on such a volume.
    """
    try:
        if mtime is None:
            st = os.stat(src)
            os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
        else:
            os.utime(dst, (mtime, mtime))
    except OSError as exc:
        log.debug(f"VolumeCache: kept copy-time mtime on {dst}: {exc}")
    try:
        shutil.copymode(src, dst)
    except OSError as exc:
        log.debug(f"VolumeCache: kept default mode on {dst}: {exc}")


def _positive_or_default(name, value, default):
    # A non-positive or non-integer pool size or chunk is a caller bug that
    # best-effort mode would otherwise swallow mid-operation (range() and
    # ThreadPoolExecutor reject floats), silently disabling the cache. bool is
    # an int subclass, so it is rejected explicitly.
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return value


def _tmp_path(path):
    return f"{path}.{os.getpid()}.{threading.get_ident()}.rpvc.tmp"


def _iter_ranges(files, chunk):
    """Lazily yield ``(offset, index, length)`` for every range of ``files``.

    Offset-major order spreads concurrent readers across every file. Files
    drop out once exhausted, so the cost is O(ranges + files) and nothing is
    materialized up front.
    """
    active = sorted((i for i, f in enumerate(files) if f.size > 0), key=lambda i: files[i].src)
    offset = 0
    while active:
        for i in active:
            yield offset, i, min(chunk, files[i].size - offset)
        offset += chunk
        active = [i for i in active if offset < files[i].size]


def _run_bounded(items, fn, workers):
    """Call fn on each of ``items`` across ``workers`` threads.

    Each thread pulls the next item from the shared iterator, so outstanding
    work stays O(workers) however many items there are; ``pool.map`` would
    submit a future per item up front. An exception escaping fn stops every
    thread from taking further items and propagates once the pool shuts down.
    """
    items = iter(items)
    lock = threading.Lock()  # generators are not thread-safe
    stop = threading.Event()

    def drain():
        while not stop.is_set():
            with lock:
                item = next(items, None)
            if item is None:
                return
            try:
                fn(item)
            except BaseException:
                stop.set()
                raise

    with ThreadPoolExecutor(max_workers=workers) as pool:
        try:
            for future in [pool.submit(drain) for _ in range(workers)]:
                future.result()
        except BaseException:
            # The waiting thread was interrupted (e.g. Ctrl-C): stop the workers
            # before the pool's shutdown waits on them, or they drain every item.
            stop.set()
            raise


def _with_range_retry(fn, *args):
    """Call fn, retrying transient FUSE errors with jittered exponential backoff."""
    for attempt in range(_RANGE_MAX_ATTEMPTS - 1):
        try:
            fn(*args)
            return
        except OSError as exc:
            if exc.errno not in _RANGE_RETRY_ERRNOS:
                raise
            time.sleep(random.uniform(0, _RANGE_RETRY_BASE_SECONDS * 2**attempt))
    fn(*args)  # final attempt: any error propagates


def _file_identity(st):
    """The generation of a file: changes when sync() replaces it.

    Size and mtime only. Inode numbers on a FUSE object-store mount are
    assigned by the filesystem and can change for an unchanged object (e.g.
    after eviction), which would reject valid files, while a cross-host
    replacement there keeps the inode and changes size or mtime instead.
    """
    return (st.st_size, st.st_mtime_ns)


def _copy_range(src, dst, offset, length, identity):
    """Copy ``length`` bytes at ``offset`` of src to the same offset of dst.

    Each call opens its own descriptors so an object-store mount serves the
    range as an independent read. The opened source must still match the
    ``identity`` pinned before the first range, so a replacement by a
    concurrent sync() seen between ranges fails the file instead of splicing
    two generations together. An object-store mount resolves reads by key, so
    a replacement landing mid-read is detected only by later ranges. A
    short read means the mirror file is smaller than the manifest says. Both
    raise without an errno, so they are not retried.
    """
    src_fd = os.open(src, os.O_RDONLY)
    try:
        if _file_identity(os.fstat(src_fd)) != identity:
            raise OSError(f"source replaced during hydrate at offset {offset}")
        dst_fd = os.open(dst, os.O_WRONLY)
        try:
            done = 0
            while done < length:
                buf = os.pread(src_fd, length - done, offset + done)
                if not buf:
                    raise OSError(f"short read: {done} of {length} bytes at offset {offset}")
                _pwrite_all(dst_fd, buf, offset + done)
                done += len(buf)
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)


def _pwrite_all(fd, buf, offset):
    view = memoryview(buf)
    written = 0
    while written < len(view):
        written += os.pwrite(fd, view[written:], offset + written)


def _silent_remove(path):
    try:
        os.remove(path)
    except OSError:
        # best-effort cleanup; a leftover temp is overwritten or ignored next run
        pass


# ----------------------------------------------------------------------- #
# background sync completion
# ----------------------------------------------------------------------- #

_pending_syncs = []


def _register_pending(thread):
    _pending_syncs[:] = [t for t in _pending_syncs if t.is_alive()]
    _pending_syncs.append(thread)


def _join_pending_syncs():
    for t in list(_pending_syncs):
        try:
            t.join(timeout=_JOIN_TIMEOUT_SECONDS)
            if t.is_alive():
                log.warn(
                    "VolumeCache: background sync did not finish within "
                    f"{_JOIN_TIMEOUT_SECONDS:.0f}s at exit; cache may be incomplete"
                )
        except Exception:
            # best-effort shutdown: a join failure must not block interpreter exit
            pass
    _pending_syncs.clear()


def _reset_pending_for_test():
    _pending_syncs.clear()


atexit.register(_join_pending_syncs)
