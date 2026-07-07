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

## How it works

- **Directory mirror.** Cached files live at `{volume_path}/.cache/{namespace}`,
  laid out at the same absolute path they occupy in the container (so the
  mirror is directly browsable — no archive format to unpack).
- **Per-file, atomic reconcile.** Each direction (`hydrate`/`sync`) walks the
  source tree and copies any file whose size differs or whose mtime is newer
  than the destination's (beyond a small tolerance for coarse network-filesystem
  mtimes). Copies are written to a temp file and atomically renamed into place
  (`os.replace`), so a crash mid-copy never leaves a half-written file visible.
- **Idempotent.** Copies preserve mtime (`shutil.copy2`), so re-running
  `hydrate`/`sync` after nothing has changed copies zero files.
- **Last-writer-wins.** Under concurrent workers, the mirror simply reflects
  whichever worker synced most recently — there's no locking or merge.
- **Safety.** Symlinked sources are never followed/copied. Hydration destinations
  are checked to resolve inside one of the configured `dirs` before any write,
  so a mirror entry can't be used to write outside the cached directories.

## Limitations

- **Cold-scale write amplification.** If N workers cold-start at the same time,
  each may miss the still-empty mirror, download the model, and sync a full
  copy back. There's no coordination between concurrent syncs.
- **Background sync on short-lived processes.** `sync()` schedules the copy on
  a daemon thread; if the process exits without going through normal
  interpreter shutdown (e.g. `os._exit`, `SIGKILL`), the atexit hook never
  runs and the sync may not complete.
