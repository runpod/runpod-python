# Change Log

## Release 1.2.1 (TBD)

### Added

- Version reported when an error is returned in serverless.
- Log level can be set with `RUNPOD_LOG_LEVEL` environment variable.

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
