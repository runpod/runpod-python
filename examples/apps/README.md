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

cancellation gives cleanup up to 30 seconds to recover an in-flight create response
and delete the pod; synchronous calls allow up to 35 seconds for the coroutine to
acknowledge cleanup after ctrl-c. repeated interrupts do not interrupt deletion.
transient delete failures are retried up to three times. cleanup errors do not
replace an existing task error or cancellation, and failed deletion retains
`job.pod_id` for recovery. cleanup that exceeds the grace period continues only
while the client's event loop remains alive. check the pod in the console after a
cleanup warning; client process exit alone does not stop billing.
