# The Serverless Worker

Both RunPod official endpoints as well as custom built endpoints function by means of a worker that fetches available jobs, passes them into a handler and then returns the output.

A worker entry point is a python file containing the command `runpod.serverless.start(config)`. An minimal worker file is shown below:

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

The handler function can either had a standard return or be a generator function. If the handler is a generator function, it will be called with the job input and the generator will be iterated over until it is exhausted.

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
