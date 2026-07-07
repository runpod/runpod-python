# Network-Volume Warm Cache (VolumeCache)

`VolumeCache` warms local directories across serverless workers using a mounted
network volume. On cold start it restores previously-synced directories (for
example a model cache) from the volume; after use it syncs newly written files
back so the next cold worker starts warm. This turns a repeated multi-GB model
download on every cold start into a one-time cost per endpoint.

It is stdlib-only and best-effort: any failure degrades to a cold worker and
never raises into your handler or the worker loop.

## Requirements

- A **network volume** attached to the endpoint (mounted at `/runpod-volume`).
- Set on serverless automatically: `RUNPOD_ENDPOINT_ID` (used to scope the cache
  per endpoint).

If no volume is mounted, every operation is a safe no-op.

## Option 1: Built-in (opt-in, zero code)

The worker can hydrate a model cache at startup and sync it after the first job,
with no changes to your handler. It is **off by default** — enable it with an
environment variable:

```bash
# Enable the built-in warm cache (opt-in)
RUNPOD_VOLUME_CACHE=1
```

When enabled and a volume is mounted, the worker:

1. Hydrates the model cache from the volume before the first job runs.
2. Syncs new files to the volume once, after the first successful job.

By default it caches the directories pointed to by `HF_HOME`, `HF_HUB_CACHE`,
and `TORCH_HOME` (whichever are set; `HF_HOME` defaults to
`~/.cache/huggingface`). Add more directories with `RUNPOD_CACHE_DIRS`.

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `RUNPOD_VOLUME_CACHE` | unset (off) | Set to `1`/`true`/`yes`/`on` to enable the built-in. |
| `RUNPOD_CACHE_DIRS` | — | Extra directories to cache, `os.pathsep`-separated (`:` on Linux). |
| `RUNPOD_VOLUME_CACHE_MAX_GB` | `50` | Prune oldest shards once the endpoint's cache exceeds this size. |
| `HF_HOME` / `HF_HUB_CACHE` / `TORCH_HOME` | — | Auto-discovered model-cache locations. |

### Example

For most model-serving workers, enabling the built-in and letting the model
download into `HF_HOME` on the first request is all that is needed:

```python
# my_worker.py
import runpod

def handler(job):
    # First cold worker downloads the model into HF_HOME; the built-in syncs it
    # to the volume. Subsequent cold workers hydrate it and skip the download.
    ...

runpod.serverless.start({"handler": handler})
```

```bash
# Template / container env
RUNPOD_VOLUME_CACHE=1
```

> Note: hydration only helps model loads that happen **after** the worker starts
> (lazy or in-handler loading). If your model loads at module import time, use
> Option 2 to place `warm()` around the load.

## Option 2: Explicit API

Import `VolumeCache` and control caching yourself. This works for any
directories, and the `warm()` context manager guarantees hydration happens
before your model load regardless of when it runs.

```python
from runpod.serverless import VolumeCache

vc = VolumeCache(dirs=["/root/.cache/huggingface"])

with vc.warm():            # hydrate on enter, sync the delta on exit
    model = load_model()   # downloads land in the cached directory
```

You can also call the phases directly when they happen at different points in
your worker's lifecycle:

```python
vc = VolumeCache(dirs=["/data/models"], namespace="my-model-cache", max_size_gb=100)

vc.hydrate()               # restore cached files (e.g. at startup)
model = load_model()       # populate the cache
vc.sync()                  # persist new files back to the volume
```

### Constructor

| Argument | Default | Purpose |
| --- | --- | --- |
| `dirs` | required | Local directories to cache. |
| `namespace` | `RUNPOD_ENDPOINT_ID` | Isolation key for the on-volume shards. Must be a single safe path component. |
| `volume_path` | `/runpod-volume` | Network-volume mount point. |
| `max_size_gb` | `None` | Prune oldest shards past this cap; `None` = no cap. |
| `best_effort` | `True` | Swallow and log errors instead of raising. Set `False` while debugging. |

## How it works

- **Per-endpoint, sharded storage.** Each worker writes its delta as its own tar
  shard under `/<volume>/.cache/<namespace>/`, published atomically. Per-worker
  shards are collision-free under concurrent workers — no shared file is
  rewritten. Hydration extracts all shards for the namespace (newest wins on
  overlap).
- **Delta only.** `sync()` packs just the files written since the last hydrate,
  not the whole cache.
- **Retention.** With `max_size_gb` set, the oldest shards are pruned once the
  namespace exceeds the cap.
- **Safety.** Extraction rejects members that would escape the configured
  directories (traversal, symlinks, absolute paths).

## Limitations

- **Cold-scale write amplification.** If N workers cold-start at the same time,
  each misses the still-empty cache, downloads the model, and writes a full-size
  shard. Total volume writes on the first scale-up are ~N×; bounded thereafter by
  retention.
- **Lost first warm on aggressive recycle.** `sync()` runs in a background thread
  after the job response. A large-model sync may not finish before the worker is
  recycled; the partial shard is cleaned up and the warm is retried on the next
  worker.

## Status

The built-in is opt-in for now (`RUNPOD_VOLUME_CACHE=1`). Whether it should
default on when a volume is present is still under review.
