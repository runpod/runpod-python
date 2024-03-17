# Change Log

## Release 1.6.2 (2/12/24)

### Fixed

- Reorder FastAPI endpoints for clarity.
- Truncate long logs to avoid overloading the server.

---

## Release 1.6.1 (2/11/24)

### Added

- User-Agent for better analytics tracking.
- Expose the ability to create container auth.
- Emulate webhooks for local testing when using `rp_serve_api` flag.

### Changed

- Updated aiohttp from 3.9.2 to 3.9.3
- [BREAKING] rename `registry_auth` to `registry_auth_id` for clarity.
- Added additional details to the FastAPI page.

---

## Release 1.6.0 (1/29/24)

### Fixed

- Rust Serverless Core Passing all tests.
- GitHub Action and Python package updates
- Changelog date typo

---

## Release 1.5.3 (1/25/24)

### Added

- Expose cuda selection when creating a pod.
- Expose `flashboot` when creating a new endpoint.
- Expose bucket name for rp_upload.
- Exposed `containerRegistryAuthId` for template creation.

### Fixed

- ReadMe typo *start_pod* -> *resume_pod*

---

## Release 1.5.2 (1/16/24)

### Fixed

- Edge case when using RunPod Serverless Core that return `None` for function output.
- Missing Job ID for logging.
- Concurrency modifier not adhering to the maximum concurrency.
- Improved in progress job tracking and counting.

### Added

- Expanded RunPod Serverless Core testing.

---

## Release 1.5.1 (1/11/24)

### Fixed

- Prevent stream from hanging on the final output.
- Allow `GET` method for `stream` test endpoint.
- Updated async runner for endpoint API calls.

### Changed

- Updated sls-core to `0.0.2`

---

## Release 1.5.0 (12/28/23)

### Added

- Optional serverless core implementation, use with environment variable `RUNPOD_USE_CORE=True` or `RUNPOD_CORE_PATH=/path/to/core.so`

### Changed

- Reduced *await asyncio.sleep* calls to 0 to reduce execution time.

---

## Release 1.4.2 (12/14/23)

### Fixed

- Added defaults for optional parameters in `rp_fastapi` to be compatible with pydantic.

## Release 1.4.1 (12/13/23)

### Added

- Local test API server includes simulated endpoints that mimic the behavior of `run`, `runsync`, `stream`, and `status`.
- Internal job tracker can be used to track job inputs.

---

## Release 1.4.0 (12/4/23)

### Changed

- [BREAKING] concurrency is now handled by `concurrency_modifier` which is a user provided function that takes in the current concurrency and returns the new concurrency. This allows for more complex concurrency logic to be implemented by the user.
- Updated the `colorama` requirement version to `>= 0.2.5, < 0.4.5`

### Added

- Streaming support for calling endpoints.

---

## Release 1.3.8 (12/1/23)

### Added

- Stream support for calling endpoints.

---

## Release 1.3.7 (11/29/23)

### Fixed

- Catch timeouts when checking for available jobs.

### Changed

- Updated and pinned aiohttp to 3.9.1

---

## Release 1.3.6 (11/23/23)

## Fixed

- Restrict version of `aiohttp` to 3.8.6 to avoid breaking changes in 3.9.0

---

## Release 1.3.5 (11/23/23)

### Fixed

- Robust `get_job` error handling
- `project.toml` now includes required dependencies

---

## Release 1.3.4 (11/14/23)

### Changed

- Logs are now JSON formatted
- Exposed logging `job_id` now `request_id`

### Added

- `get_endpoints` exposed to return all endpoints for a given user

---

## Release 1.3.3 (11/8/23)

### Added

- Method of creating logs with job id.

### Fixed

- Reduced polling when checking for job completion.
- Removed print statements for endpoint calls.
- Serverless progress updates no longer restricted to only strings.

## Changed

- Removed `pillow` dependency.
- Removed `python-dotenv` dependency.
- Removed `setuptools_scm` from required dependencies.

---

## Release 1.3.2 (11/3/23)

### Changed

- Removed `setup.cfg` and moved all configuration to `setup.py`
- [BETA] Clean exit CLI when ctl+c is pressed.

---

## Release 1.3.1 (10/30/23)

### Added

- `test_output` can be passed in as an arg to compare the results of `test_input`
- Generator/Streaming handlers supported with local testing
- [BETA] CLI DevEx functionality to create development projects.

---

## Release 1.3.0 (10/12/23)

### Changed

- Backwards compatibility with Python >= 3.8
- Consolidated install dependencies to `requirements.txt`

### Fixed

- Corrected helper link for rp_uploads, closes issue #169

---

## Release 1.2.6 (10/6/23)

### Changed

- Force `urllib3` logging to `WARNING` level to avoid spamming the console if global logging level is set to `DEBUG`.

---

## Release 1.2.5 (10/5/23)

### Fixed

- Handler called twice.
- Default container disk size removed if template is provided when creating a new pod.

---

## ~~Release (Patch) 1.2.3 (10/4/23)~~ Replaced by 1.2.5

### Fixed

- Job outputs that were not dictionaries, bool, or str were swallowed by the serverless worker. This has been fixed.

---

## ~~Release 1.2.2 (10/4/23)~~ Replaced by 1.2.5

### Added

- User queries and mutations are now available in the python API wrapper.
- `start_ssh` added with default `True` when creating new pods.
- `network_volume_id` can now be passed in when creating new pods, correct data center is automatically selected.
- `template_id` can now be passed in when creating new pods.

### Changed

- Dependencies updated to latest versions.
- Reduced circular imports for version reference.
- `support_public_ip` is not default to `True` when creating new pods.

### Fixed

- Reduce pool_connections for ping requests to 10.
- Double timeout for ping requests.

---

## Release 1.2.1 (9/22/23)

### Added

- Version reported when an error is returned in serverless.
- Log level can be set with `RUNPOD_LOG_LEVEL` environment variable.
- SIGTERM handler initialized when starting serverless worker to avoid hung workers.
- Progress update method exposed `runpod.serverless.progress_update` can be called with the job object and string.

### Fixed

- Region is included when using S3 storage via rp_upload, automatically filled in for Amazon S3 buckets and Digital Ocean Spaces.

---

## Release 1.2.0 (8/29/23)

### Added

- Command Line Interface (CLI)
- Can generate a credentials file from the CLI to store your API key.
- `get_gpu` now supports `gpu_quantity` as a parameter.

### Changed

- Minimized the use of pytests in favor of unittests.
- Re-named `api_wrapper` to `api` for consistency.
- `aiohttp_retry` packaged replaced `rp_retry.py` implementation.

### Fixed

- Serverless bug that would not remove task if it failed to submit the results.
- Added missing `get_pod`
- Remove extra print statement when making API calls.
