# RunPod Pod Worker

To convert a Pod to a Worker, you need to add the following annotations to the Pod:

## Environment Variables

```bash
# Development
DEBUG= # Set to 'true' to enable debug mode, otherwise leave blank
TEST_LOCAL= # Set to 'true' to test locally, otherwise leave blank

# Configurations
MODEL= # Model to use (Default: stable_diffusion)
EXECUTION_TIMEOUT= # Timeout for execution in milliseconds (Default: 300000)
TERMINATE_IDLE_TIME= # Time in milliseconds to wait before terminating idle pods (Default: 60000)

# API Endpoints
AI_API_KEY= # Serverless API Key
WEBHOOK_GET_WORK= # URL to get work from
WEBHOOK_POST_OUTPUT= # URL to post output to
WEBHOOK_PING= # URL to ping
PING_INTERVAL= # Interval in milliseconds to ping the API (Default: 10000)

# S3 Bucket
BUCKET_ENDPOINT_URL= # S3 bucket endpoint url
BUCKET_ACCESS_KEY_ID= # S3 bucket access key id
BUCKET_SECRET_ACCESS_KEY= # S3 bucket secret access key
```

### Additional Variables

These are variables that are accessed from the RunPod container and not required to be set manually:

```bash
# Pod Information
RUNPOD_POD_ID= # Pod ID
RUNPOD_API_KEY= # Pod API Key
```

## Error Handling

If an error occurs, the worker will send a message to the API with the error message and the job will be marked as failed.

To report a job error call `job.error(worker_id, job_id, error_message)`.

---

## Worker Lifecycle Management

The duration of the worker is managed within the [lifecycle](../PodWorker/modules/lifecycle.py) module.

The worker starts with a TTL as specified by the environment variable `TERMINATE_IDLE_TIME` or defaults to 60 seconds as specified with `self.ttl`. When a new job is received, a `work_in_progress` flag is set. When the job is completed, the `work_in_progress` flag is cleared and the TTL is reset. If the `work_in_progress` flag is not cleared within the `work_timeout` period, the worker will exit.

 If the worker does not receive a new job within idle period, the worker will exit.

## Tracking TTL

On working initialization a time tracking thread is started, this thread monitors the workers TTL and exits the worker if the TTL has expired.

## Worker Zero

Worker zero can have a modified TTL behavior, to flag a worker as worker zero set `self.is_worker_zero` to `True`. Worker zero will not run the TTL check thread.

## Worker Seppuku

When the TTL of a worker has expired the function `self.seppuku` is called. On exit the following actions are taken:

- RunPod API call to delete the pod

## Local Testing

To test locally, create the file `test_inputs.json` in the root directory that contains the following:

```json
{
    "id": "LOCAL-TEST",
    "input":{}
}
```

The inputs should match the inputs your model would expect to see from the API. Then set `TEST_LOCAL` to `true` in the .env file and run the worker.
