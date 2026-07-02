# runtimes

worker runtime scripts and the docker images built from them. images
are built and pushed by the `runtimes` github actions workflow.

## cold-start model

deployed workers do no dependency resolution. `rp deploy` vendors the
full runtime environment (the runpod package plus every python
dependency, resolved for the worker platform) into the build artifact
under `env/`. at cold start the bootstrap:

1. **locate** the app tree: a host-provided pre-unpacked directory if
   available, else extract the artifact tarball once (marker file
   short-circuits warm restarts)
2. **attach** `env/` and the source tree to the worker's PYTHONPATH,
   vendored env first so it wins over image packages
3. **verify** size-excluded packages (torch and friends) exist in the
   image, installing only if genuinely absent (loudly, as a fallback)
4. **serve** exec the worker runtime

torch-family packages are excluded from artifacts by size and expected
from the gpu worker images; the exclusion list is recorded in the
manifest as `excludedPackages`.

if any phase fails, the worker does not crash-loop silently: queue
workers answer jobs with a structured `BootstrapError`, api workers
answer http requests with it.

live mode (`rp dev`) has no artifact; source arrives per request and
the bootstrap only ensures the runtime package is importable (a no-op
on the baked images).

## images

every runtime kind ships a cpu and a gpu variant per python version.
gpu variants build on `runpod/gpu-base:pyX.Y-{tag}` (python:X.Y-slim
plus the torch family), which preinstalls exactly the packages the
build auto-excludes from artifacts. the two lists must stay in sync:
`SIZE_PROHIBITIVE_PACKAGES` in `runpod/apps/build.py` and the pip
install in `gpu-base/Dockerfile`.

| image | base | use |
|---|---|---|
| `runpod/gpu-base:py{3.10-3.14}-{tag}` | `python:X.Y-slim` + torch | shared gpu base |
| `runpod/queue:py{3.10-3.14}-{tag}` | `python:X.Y-slim` | cpu queue endpoints |
| `runpod/queue-gpu:py{3.10-3.14}-{tag}` | gpu-base | gpu queue endpoints |
| `runpod/api:py{3.10-3.14}-{tag}` | `python:X.Y-slim` | cpu api endpoints |
| `runpod/api-gpu:py{3.10-3.14}-{tag}` | gpu-base | gpu api endpoints |
| `runpod/task:py{3.10-3.14}-{tag}` | `python:X.Y-slim` | cpu tasks |
| `runpod/task-gpu:py{3.10-3.14}-{tag}` | gpu-base | gpu tasks |

image selection lives in `runpod.apps.images`; dev sessions and tasks
pick the variant matching the local python so pickled payloads stay
compatible.

custom images work everywhere: the sdk injects `bootstrap.py` (queue/
api) or `runner.py` (task) base64-encoded in an env var and boots it
via `dockerArgs`. both scripts are stdlib-only, so any image with a
python3 binary works. for deployed resources the vendored env then
provides everything else, including the runpod package itself. builds
targeting custom images vendor the torch family too (no auto
exclusion), since only the builtin gpu images guarantee it.

## task

single-shot task runner for `@app.task` pods. one pod runs one
function: the sdk deploys a pod, waits for `/ping`, posts a
`FunctionRequest` to `/execute` (or `/submit` for spawn), collects the
result, and terminates the pod.

the wire protocol (`FunctionRequest` / `FunctionResponse`) is defined
in `runpod.apps.protocol`; the runner and sdk must stay in sync with
it. `runner.py` deliberately avoids importing the runpod package so it
can run standalone on arbitrary images.
