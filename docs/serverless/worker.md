# RunPod Pod Worker

To convert a Pod to a Worker, you need to add the following annotations to the Pod:

## Environment Variables

```bash
# Development
RUNPOD_DEBUG= # Set to 'true' to enable debug mode, otherwise leave blank
RUNPOD_DEBUG_LEVEL= # ERROR, WARN, INFO, DEBUG

# API Endpoints
RUNPOD_AI_API_KEY= # Serverless API Key
RUNPOD_WEBHOOK_GET_JOB= # URL to get job work from
RUNPOD_WEBHOOK_POST_OUTPUT= # URL to post output to
RUNPOD_WEBHOOK_PING= # URL to ping
RUNPOD_PING_INTERVAL= # Interval in milliseconds to ping the API (Default: 10000)

RUNPOD_ENDPOINT_ID= # Endpoint ID

# Realtime
RUNPOD_REALTIME_PORT= # Port to listen on for realtime connections (Default: None)
RUNPOD_REALTIME_CONCURRENCY= # Number of workers to spawn (Default: 1)
```

### Additional Variables

These are variables that are accessed from the RunPod container and not required to be set manually:

```bash
# Pod Information
RUNPOD_POD_ID= # Pod ID
```

## Error Handling

If an error occurs, the worker will send a message to the API with the error message and the job will be marked as failed.

To report a job error call `job.error(worker_id, job_id, error_message)`.

---

## Worker Lifecycle Management

The duration of the worker is managed within the [lifecycle](../PodWorker/modules/lifecycle.py) module.

The worker starts with a TTL as specified by the environment variable `TERMINATE_IDLE_TIME` or defaults to 60 seconds as specified with `self.ttl`. When a new job is received, a `work_in_progress` flag is set. When the job is completed, the `work_in_progress` flag is cleared and the TTL is reset. If the `work_in_progress` flag is not cleared within the `work_timeout` period, the worker will exit.

 If the worker does not receive a new job within idle period, the worker will exit.

## Local Testing

To test locally, create the file `test_input.json` in the root directory that contains the following:

```json
{
    "id": "LOCAL-TEST",
    "input":{}
}
```

If the required webhook environment variables are not set, the worker will default to local testing.
