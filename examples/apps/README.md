# apps examples

each file is a self-contained app demonstrating one feature, runnable
directly and asserting on its own results — the set doubles as an
end-to-end test suite against production.

| example | shows |
|---|---|
| `01_hello_world` | one queue function, one `.remote()` call |
| `02_invocation_styles` | `.remote()`, `.remote.aio()`, `.spawn()`, `.local()` |
| `03_gpu` | gpu shorthand selection, env vars, torch/cuda |
| `04_dependencies` | pip `dependencies` and apt `system_dependencies` |
| `05_api_service` | `@app.api` class with `@init`/`@get`/`@post`, stateful workers |
| `06_tasks` | ephemeral pods (cpu and gpu) via `@app.task` |
| `07_pipelines` | workers calling sibling resources with nested `.remote()` |
| `08_volumes` | shared network volume between tasks, auto placement |
| `09_secrets` | platform secrets as env vars (needs `rp secret add ex-demo-secret`) |
| `10_cached_models` | platform-staged huggingface weights via `Model` |
| `11_train_eval` | train on one gpu pod, save to a volume, eval on another |
| `12_custom_image` | custom container image with runtime bootstrap |

## running

one example:

```bash
rp dev examples/apps/01_hello_world.py --once
```

the whole suite:

```bash
python examples/apps/run_all.py            # sequential
python examples/apps/run_all.py --jobs 4   # parallel
python examples/apps/run_all.py 01 07      # subset by prefix
```

`09_secrets` needs a secret to exist first:

```bash
rp secret add ex-demo-secret --value anything
```

`12_custom_image` bootstraps the worker runtime onto a bare image by
installing the runpod package at cold start. until the version with
`runpod.runtimes` is on pypi, point the bootstrap at a tarball of the
branch (plain https, not git+ — slim images have no git):

```bash
export RUNPOD_PACKAGE_SPEC=https://github.com/runpod/runpod-python/archive/refs/heads/feat/apps-sdk.tar.gz
```
