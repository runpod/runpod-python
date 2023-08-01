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
