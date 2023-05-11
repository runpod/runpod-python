# Realtime Worker

Workers can be used to run jobs in realtime, this is particularly useful for running jobs that have minimal inference time. When running a worker in realtime the results of the job is returned directly when a request is made.

**Note:** At the time of writing users must contact runpod.io to enable realtime workers for their account.

## Initializing the Worker

A worker will be launched as a realtime worker if the `RUNPOD_REALTIME_PORT` environment variable is set. This port is the port that uvicorn will listen on for requests.

### Concurrent Requests

By default the realtime worker will only process one request at a time. This can be changed by setting the `RUNPOD_REALTIME_CONCURRENCY` environment variable. This variable should be set to the number of concurrent requests that should be processed.
