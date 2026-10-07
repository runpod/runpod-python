# app examples

each file is a self-contained app showing one way to use the sdk.
run any of them live with `rp flash dev`:

```bash
rp flash dev examples/apps/hello_world.py
```

| example | shows |
|---|---|
| `hello_world` | one queue function, one `.remote()` call |
| `streaming` | generator functions, `.stream()` chunks, `job.stream()` |
| `gpu_inference` | gpu selection, dependencies, a cuda benchmark |
| `web_service` | `@app.api` class with routes and per-worker state |
| `train_and_eval` | gpu tasks sharing checkpoints through a volume |

edit a file while the session is running, then press enter to re-run —
workers pick up the new code automatically. the exhaustive
feature-by-feature suite lives in [`tests/e2e/examples`](../../tests/e2e/examples).

`await task.spawn.aio(...)` returns a task job that owns its pod.
`await job.wait(timeout=...)` attempts to terminate that pod when waiting finishes,
including timeout, task failure, or cancellation. use `await job.cancel()` to
abandon a spawned task explicitly. leaving the client without waiting or cancelling
does not cancel intentional detached work.

cancellation waits for bounded cleanup and retries transient deletion failures.
failed deletion retains `job.pod_id`; check the console after a cleanup warning.
active tasks can run indefinitely, including detached work after client exit.
`remote()` and `job.wait()` impose no execution timeout by default. pod deletion
requires a working control-plane API and pod-scoped credentials.

## Storage

`NetworkVolume(name_or_id, size=50, datacenter=None, create=True)` references
datacenter-local storage. New volumes use one catalog capability snapshot per
provisioning run to select a datacenter with network storage support and hardware
stock for every resource sharing the volume, respecting their datacenter pins.
An unsupported explicit volume pin fails before creation instead of relocating;
catalog lookup failures also stop creation. Existing volumes retain their IDs and
datacenters regardless of new-volume eligibility. Their consumers must still be
schedulable in that datacenter. `GlobalVolume(name_or_id, create=True)`
references global storage without a datacenter constraint. Both inherit from
the abstract `Volume` base, resolve by name or ID, and create missing storage
when provisioning remote compute. Set `create=False` to require existing storage.
Global-volume lookup and creation use GraphQL; network volumes use REST.

Declare attachments with `mounts={"/path": volume}`. Tasks support one network
and one global volume at distinct, non-overlapping paths. Queue and API resources
support one volume at `/runpod-volume`; global storage requires a GPU endpoint.

Inside worker code, `volume.path` returns the configured mount path. The runtime
binds declared references and resolved IDs before importing user code. Access
raises if the volume is unmounted or has multiple bindings. Evaluate `.path`
inside remote functions, not during local module discovery. Calling `.local()`
does not mount remote storage on the client machine.

