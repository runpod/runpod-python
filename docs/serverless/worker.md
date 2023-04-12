# The Serverless Worker

## Logging

The worker outputs logs to the console at different points in the workers lifecycle. These logs can be used to debug issues with the worker or handler. There are four logging levels that can be used to control the verbosity of the logs:

   1. `DEBUG` (Default) - Outputs all logs, including debug logs.

   2. `INFO` - Outputs all logs except debug logs.

   3. `WARNING` - Outputs only warning and error logs.

   4. `ERROR` - Outputs only error logs.

To set the logging level, set the `RUNPOD_DEBUG_LEVEL` environment variable to one of the above logging levels. For example, to set the logging level to `INFO`, set the `RUNPOD_DEBUG_LEVEL` environment variable to `INFO`.

## Error Handling

The worker is designed to handle errors raised by the handler gracefully. If the handler raises an error, the worker will capture this error and return it as the job output along with the stack trace.

If you want to return a custom error within the handler, this can be accomplished by returning a dictionary with a top-level key of `error` and a value of the error message. The worker will then return this error message as the job output.

### Example

```python
def handler_with_custom_error(job):
    if job["id"] == "invalid_job":
        return {"error": "Invalid job ID"}
    else:
        # Handle the job and return the output
        return {"output": "Job completed successfully"}
```

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
