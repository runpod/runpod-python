# runtimes

worker runtime scripts and the docker images built from them. images
are built and pushed by the `runtimes` github actions workflow on
release.

## task

single-shot task runner for `@app.task` pods. one pod runs one
function: the sdk deploys a pod on one of these images, waits for
`/ping`, posts a `FunctionRequest` to `/execute` (or `/submit` for
spawn), collects the result, and terminates the pod.

| image | base | use |
|---|---|---|
| `runpod/task:py{3.10,3.11,3.12}-{tag}` | `python:X.Y-slim` | cpu tasks |
| `runpod/task-gpu:{tag}` | `runpod/pytorch` | gpu tasks |

`runner.py` is stdlib-only (cloudpickle optional, used for argument
and result serialization when present). this keeps the env-injection
fallback working: when a user supplies a custom `image=` that does not
have the runner baked in, the sdk ships `runner.py` base64-encoded in
a pod env var and boots it via `dockerArgs`, so any image with a
python3 binary works.

the wire protocol (`FunctionRequest` / `FunctionResponse`) is defined
in `runpod.apps.protocol`; the runner and sdk must stay in sync with
it. `runner.py` deliberately avoids importing the runpod package so it
can run standalone on arbitrary images.
