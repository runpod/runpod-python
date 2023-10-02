# Change Log

## Release 1.2.1 (89/22/23)

### Added

- Version reported when an error is returned in serverless.
- Log level can be set with `RUNPOD_LOG_LEVEL` environment variable.
- SIGTERM handler initialized when starting serverless worker to avoid hung workers.
- Progress update method exposed `runpod.serverless.progress_update` can be called with the job object and string.

### Fixed

- Region is included when using S3 storage via rp_upload, automatically filled in for Amazon S3 buckets and Digital Ocean Spaces.

## Release 1.2.0 (8/29/23)

### Added

- Command Line Interface (CLI)
- Can generate a credentials file from the CLI to store your API key.
- `get_gpu` now supports `gpu_quantity` as a parameter.

### Changes

- Minimized the use of pytests in favor of unittests.
- Re-named `api_wrapper` to `api` for consistency.
- `aiohttp_retry` packaged replaced `rp_retry.py` implementation.

### Fixed

- Serverless bug that would not remove task if it failed to submit the results.
- Added missing `get_pod`
- Remove extra print statement when making API calls.
