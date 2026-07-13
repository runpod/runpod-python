# app examples

each file is a self-contained app showing one way to use the sdk.
run any of them live with `rp dev`:

```bash
rp dev examples/apps/hello_world.py
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
