# Network-Volume Warm Cache (VolumeCache)

`VolumeCache` warms local directories across serverless workers using a mounted
network volume. It keeps a browsable mirror of your cache directories on the
volume and reconciles it against the container on each use: on cold start it
restores previously-cached files into place, and after use it copies newly
written files back to the volume. This turns a repeated multi-GB model
download on every cold start into a one-time cost per endpoint.

It is stdlib-only and best-effort: any failure degrades to a cold worker and
never raises into your handler or worker loop.

## Requirements

- A **network volume** attached to the endpoint (mounted at `/runpod-volume`).
- Set on serverless automatically: `RUNPOD_ENDPOINT_ID` (used to scope the
  mirror per endpoint/namespace).

If no volume is mounted, or the namespace is empty, every operation is a safe
no-op.

## Usage

`VolumeCache` is a context-manager closure — using it around a model load
hydrates the cache before the block runs and syncs any changes back after:

```python
from runpod.serverless import VolumeCache

with VolumeCache(dirs=["/root/.cache/huggingface"]):
    model = load_model()   # downloads land in the cached directory
```

- **On enter**, `hydrate()` copies files that are missing or newer on the
  volume mirror into the container.
- **On exit**, `sync()` copies files that are missing or newer in the
  container onto the volume mirror. By default this runs on a background
  daemon thread and returns immediately, so the `with` block doesn't block on
  the sync; a process-exit hook joins any outstanding syncs so short-lived
  processes (including local test runs) still complete the sync before
  exiting.

You can also call the phases directly when they happen at different points in
your worker's lifecycle:

```python
vc = VolumeCache(dirs=["/data/models"], namespace="my-model-cache")

vc.hydrate()                        # restore cached files (e.g. at startup)
model = load_model()                # populate the cache
vc.sync(background=False)           # persist new files back to the volume, inline
```

## Constructor

| Argument | Default | Purpose |
| --- | --- | --- |
| `dirs` | required | Local directories to cache. |
| `namespace` | `RUNPOD_ENDPOINT_ID` | Isolation key for the on-volume mirror. Must be a single safe path component. |
| `volume_path` | `/runpod-volume` | Network-volume mount point. |
| `best_effort` | `True` | Swallow and log errors instead of raising. Set `False` while debugging. |
| `max_workers` | `min(32, (os.cpu_count() or 4) * 4)` | Thread count for parallel copy of large files on sync (I/O-bound). |
| `hydrate_workers` | `max_workers` if set, else `8` | Concurrent range reads when hydrating large files. |
| `hydrate_chunk_size` | `512 MiB / hydrate_workers`, at most `64 MiB` | Bytes per hydrate range. Peak hydrate memory is about `hydrate_workers * hydrate_chunk_size`, so the default keeps it at or below 512 MiB. |

## How it works

- **Size-bucketed mirror.** Cached files live at
  `{volume_path}/.cache/{namespace}`, split by size: files below 256 KiB are
  packed into a single `small.tar` archive (collapsing per-file metadata
  round-trips on the network volume), and larger files are copied unpacked
  into a `big/` subdirectory, preserving their original relative path. A
  versioned `manifest.json` — written last — records file metadata (size,
  mtime) for every cached file and is the commit marker for a complete
  mirror; a mirror without a valid, current-version manifest is treated as
  absent.
- **Incremental large files, whole-archive small files.** `big/` transfers
  are diffed per file by size/mtime against the manifest, so unchanged large
  files are skipped. The `small.tar` archive is re-packed as a whole whenever
  any small file has changed, since unpacking and re-diffing many tiny files
  individually is slower than the network volume's per-file overhead.
- **Parallel copy.** On sync, large files are copied whole across a thread
  pool sized by `max_workers` (default `min(32, (os.cpu_count() or 4) * 4)`),
  since the work is I/O-bound.
- **Range-parallel hydrate.** On hydrate, every large file is split into
  `hydrate_chunk_size` ranges, and one pool of `hydrate_workers` threads reads
  the ranges of all files at once, each on its own file descriptor.
  Object-store mounts such as Global Store serve one sequential stream at
  roughly 140 MiB/s, and reading several whole files at once doesn't add up,
  but independent range reads scale to the host's network (~1.1 GiB/s
  measured). Transient `ENOMEM`/`EIO`/`EAGAIN` errors, which such mounts
  return under load, are retried with jittered exponential backoff. A file is assembled in a temp
  file and renamed into place only when every range has copied, so a failed
  file leaves any existing local copy untouched and is simply not restored.
  A mirror file whose size differs from the manifest, or whose size or mtime
  changes between ranges because a concurrent `sync()` replaced it, is
  likewise not restored. This is a best-effort check: an object-store mount
  can serve a replacement that lands in the middle of a single range read
  without it being visible. Keep `hydrate_workers * hydrate_chunk_size` near the default: past ~1 GiB in
  flight, Global Store starts failing reads with `ENOMEM`. The default of 8 x
  64 MiB ranges is about 2x faster than 64 x 8 MiB on network volumes and
  within run-to-run noise on Global Store; if first-hydrate times on fresh
  Global Store workers matter, `hydrate_workers=64` (8 MiB ranges) is worth
  measuring.
- **Packing/extraction.** Packing the small-file archive uses the `tar`
  binary when available (falling back to the stdlib `tarfile` module
  otherwise). Extraction always goes through `tarfile`, validating every
  member's resolved path against the configured `dirs` before writing.
- **Idempotent.** Re-running `hydrate`/`sync` after nothing has changed
  copies zero files and does not repack `small.tar`. `manifest.json` is still
  refreshed on every `sync` call — a small atomic write that also drops
  entries for files deleted locally since the last sync.
- **Last-writer-wins.** Under concurrent workers, the mirror simply reflects
  whichever worker synced most recently — there's no locking or merge.
- **Safety.** Symlinked sources are never followed/copied. Every archive
  member and every big-file destination is checked to resolve inside one of
  the configured `dirs` before any write, so a mirror entry can't be used to
  write outside the cached directories.
- **Logging.** Each hydrate that restores files, retries a range, or gives up
  on a range logs one summary line:
  `hydrated N file(s) from <mirror> (range retries: R, ranges given up: G)`.
  A range is given up when its retries run out or its error is not
  transient. The file it belongs to is then not restored, and its other
  ranges are skipped without being counted. Every retry is logged at debug
  level with the errno name, the attempt number, the file, and the range
  offset. A hydrate that passes 64 range retries logs one warning, because
  the mount is then under sustained pressure and backoff is slowing the
  hydrate down. When a volume rejects `utime`/`chmod` with `EPERM` (as Global
  Store does), the sync logs that once at debug level and skips metadata
  updates for the rest of its files.

## Limitations

- **Cold-scale write amplification.** If N workers cold-start at the same time,
  each may miss the still-empty mirror, download the model, and sync a full
  copy back. There's no coordination between concurrent syncs.
- **Background sync on short-lived processes.** `sync()` schedules the copy on
  a daemon thread; if the process exits without going through normal
  interpreter shutdown (e.g. `os._exit`, `SIGKILL`), the atexit hook never
  runs and the sync may not complete.
- **Orphaned big files are never pruned.** If a large file is deleted or
  renamed locally, its `big/<relpath>` copy stays on the volume — hydrate
  is manifest-driven and simply ignores it, but volume space grows across
  model-version swaps. Same behavior as the prior flat-mirror design.
