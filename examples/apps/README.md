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
`await job.wait(timeout=...)` terminates that pod when waiting finishes, including
timeout, task failure, or cancellation. use `await job.cancel()` to abandon a
spawned task explicitly. a failed termination retains `job.pod_id` for cleanup.
