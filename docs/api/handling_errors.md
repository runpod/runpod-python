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
