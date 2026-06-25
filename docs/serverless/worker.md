# The Serverless Worker

Both Runpod official endpoints as well as custom-built endpoints function by means of a worker that fetches available jobs, passes them into a handler and then returns the output.

A worker entry point is a python file containing the command `runpod.serverless.start(config)`. A minimal worker file is shown below:

```python
import runpod

def handler(job):
    # Handle the job and return the output
    return {"output": "Job completed successfully"}

runpod.serverless.start({"handler": handler})
```

## config

The `config` parameter is a dictionary containing the following keys:

| Key       | Type       | Description                                                  |
|-----------|------------|--------------------------------------------------------------|
| `handler` | `function` | The handler function that will be called with the job input. |

### handler

The handler function can either have a standard return or be a generator function. If the handler is a generator function, it will be called with the job input and the generator will be iterated over until it is exhausted.

## Worker Refresh

For more complex operations where you are downloading files or making changes to the worker, it can be beneficial to refresh the worker between jobs. This can be accomplished by enabling a `refresh_worker` worker flag in one of two ways:

   1. Enable on start with `runpod.serverless.start({"handler": handler, "refresh_worker": True})`, this will refresh the worker after every job return, even if the handler raises an error.

        Example:

        ```python
        from runpod.serverless import start

        def handler(job):
            # Handle the job and return the output
            return {"output": "Job completed successfully"}

        start({"handler": handler, "refresh_worker": True})
        ```

   2. Return `refresh_worker=True` as a top-level dictionary key in the handler return. This can selectively be used to refresh the worker based on the job return.

        Example:

        ```python
        def handler_with_selective_refresh(job):
            if job["input"].get("refresh", False):
                # Handle the job and return the output with refresh_worker flag
                return {"output": "Job completed successfully", "refresh_worker": True}
            else:
                # Handle the job and return the output
                return {"output": "Job completed successfully"}
        ```

## Stopping Individual Jobs

A worker can process more than one job concurrently. When a single request is cancelled, expires, or times out, the Runpod server signals the worker to stop just that request without affecting the worker's other in-progress jobs. The worker continuously polls a dedicated stop channel; the server is expected to hold each request open (long-poll) until a stop signal is available or the poll times out. When a signal arrives, the worker cancels the task running the matching job, so a stopped job no longer consumes worker time.

No handler changes are required to support this. Async handlers that hold resources can perform cleanup by catching `asyncio.CancelledError`, but they **must re-raise** it after cleaning up. Swallowing the cancellation makes the worker report the job as completed instead of stopped.

## See Also

- [Worker Fitness Checks](./worker_fitness_checks.md) - Validate your worker environment at startup
- [Local Testing](./local_testing.md) - Test your worker locally before deployment
- [Realtime API](./worker_realtime.md) - Build realtime endpoints with streaming responses
