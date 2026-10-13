# Handling API errors

Authentication failures raise `AuthenticationError`:

```python
import runpod

try:
    runpod.get_pods()
except runpod.error.AuthenticationError as err:
    print(err)
```

REST API problem responses raise `QueryError`. The exception includes the HTTP
status code, request method and path, and request-validation errors when present:

```python
try:
    runpod.create_pod(
        "training",
        "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
        "NVIDIA GeForce RTX 4090",
    )
except runpod.error.QueryError as err:
    print(err.status_code)
    print(err.query)
    print(err.errors)
```

## REST rate limits

REST calls share Runpod's minute, hour, and day quotas across routes within the
process, keyed by API origin and API key. Both synchronous and asynchronous calls
reserve the remaining budget advertised by the `RateLimit` response header and
wait when a window is exhausted. Separate processes and different API keys for the
same account cannot coordinate their local budgets.

HTTP 429 responses honor `Retry-After` in seconds or HTTP-date format. Missing or
invalid retry hints use capped exponential backoff with jitter. By default, a call
allows three retries and at most 60 seconds of cumulative rate-limit sleeps.
Network failures and other HTTP errors are not retried, since a failed response
can follow a successful resource mutation.

An exhausted retry or wait budget raises `RateLimitError`, a `QueryError` subclass
with `status_code=429` and a `retry_after` delay in seconds. A successful call that
uses the last available token still returns immediately; subsequent calls wait or
raise if the required delay exceeds their wait budget.

```python
try:
    pods = runpod.get_pods()
except runpod.error.RateLimitError as err:
    print(f"Quota exhausted. Retry after {err.retry_after:.1f} seconds.")
```

The low-level `runpod.api.rest.run_rest_request` and `run_rest_request_async`
functions accept `max_retries` and `max_rate_limit_wait` keyword arguments. Set
`max_retries=0` to surface the first 429, or `max_rate_limit_wait=0` to fail rather
than sleep for an exhausted quota. `timeout` applies separately to each HTTP
attempt. Async quota waits are cancellable and do not block the event loop.

These protections apply to the management REST API transport. GraphQL calls and
Serverless job execution use separate transports.
