# Examples

## Before you start

```bash
pip install runpod
rp login
```

`rp login` stores your API key, and every example picks it up from there. If
`RUNPOD_API_KEY` is set in your environment, the examples use that key
instead.

## Which folder to start with

| Folder | What it shows | Needs |
|---|---|---|
| [`apps/`](apps) | Python functions that run on cloud GPUs and CPUs, started with `rp flash dev` | An account. Start here if you are new. |
| [`serverless/`](serverless) | Handlers for your own serverless worker image | Nothing. They run locally. |
| [`endpoints/`](endpoints) | Sending jobs to an existing serverless endpoint | An endpoint id |
| [`api/`](api) | Creating templates, endpoints, and registry credentials | An account |
| [`rest_wrapper.py`](rest_wrapper.py) | Pod lifecycle: create, stop, resume, terminate | An account. Creates a billable GPU pod. |

## Running the serverless handlers locally

```bash
cd examples/serverless
python simple_handler.py --test_input '{"input": {"name": "world"}}'
```

Or start a local API server and call it:

```bash
python simple_handler.py --rp_serve_api
curl -X POST http://localhost:8000/runsync \
  -H "Content-Type: application/json" \
  -d '{"input": {"name": "world"}}'
```

Run with no arguments, a handler looks for a `test_input.json` file in the
current directory.

## Getting an endpoint id for `endpoints/`

Each `endpoints/` example has a `YOUR_ENDPOINT_ID` placeholder. To use an
endpoint you already have, list them with `rp endpoint list`. To create one
for trying the examples, run [`api/create_endpoint.py`](api/create_endpoint.py),
which deploys `runpod/mock-worker` and prints the new id.

`endpoints/streaming.py` needs a worker that streams: a generator handler, or
`runpod/mock-worker` started with `--generator`.

## Cost and cleanup

- Endpoints with `workers_min=0` cost nothing while idle. Delete them with
  `rp endpoint delete ENDPOINT_ID`.
- Templates stay on your account. Delete them from the Templates page in the
  console.
- `rest_wrapper.py` terminates its pod at the end, including when a step
  fails.
- `rp flash dev` deletes the endpoints it created when the session ends.
  Network volumes created by `apps/` examples are not deleted; list them with
  `rp volume list` and remove them with `rp volume delete`.
