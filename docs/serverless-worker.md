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
AI_API_KEY= # infer-ai API Key
WEBHOOK_GET_WORK= # URL to get work from
WEBHOOK_POST_OUTPUT= # URL to post output to

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
