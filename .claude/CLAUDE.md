# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview
Runpod Python is a dual-purpose library: a GraphQL API wrapper for Runpod cloud services and a serverless worker SDK for custom endpoint development. The project supports both synchronous and asynchronous programming patterns.

## Development Environment
- **Python versions**: 3.8-3.11 (3.8+ required)  
- **Build system**: setuptools with setuptools_scm for automatic versioning from git tags
- **Dependency management**: uv with uv.lock for deterministic builds
- **Package installation**: `uv sync --group test` for development dependencies
- **Lock file**: `uv.lock` ensures reproducible dependency resolution

## Build & Development Commands

### Environment Setup
```bash
# Install package with development dependencies
uv sync --group test

# Install all dependency groups (includes dev and test)
uv sync --all-groups

# Install from source (editable) - automatically done by uv sync
uv sync --group test

# Install latest development version
uv pip install git+https://github.com/runpod/runpod-python.git
```

### Testing
```bash
# Run full test suite with 90% coverage requirement
uv run pytest

# Run tests with coverage report (matches CI configuration)
uv run pytest --durations=10 --cov=runpod --cov-report=xml --cov-report=term-missing --cov-fail-under=90

# Run specific test modules
uv run pytest tests/test_api/
uv run pytest tests/test_serverless/
uv run pytest tests/test_cli/

# Test with timeout (120s max per test) - configured in pytest.ini
uv run pytest --timeout=120 --timeout_method=thread
```

### CLI Development & Testing
```bash
# Test CLI commands (entry point: runpod.cli.entry:runpod_cli)
uv run runpod --help
uv run runpod config      # Configuration wizard
uv run runpod pod         # Pod management
uv run runpod project     # Serverless project scaffolding
uv run runpod ssh         # SSH connection management
uv run runpod exec        # Remote execution

# Local serverless worker testing
uv run python worker.py --rp_serve_api    # Start local test server for worker development
```

### Package Building
```bash
# Build distributions (uses setuptools_scm for versioning)
uv build

# Verify package
uv run twine check dist/*

# Version is automatically determined from git tags
# No manual version updates needed in code
```

## Code Architecture

### Dual-Mode Operation Pattern
The library operates in two distinct modes:
1. **API Mode** (`runpod.api.*`): GraphQL wrapper for Runpod web services
2. **Worker Mode** (`runpod.serverless.*`): SDK for building serverless functions

### Key Modules Structure

#### `/runpod/api/` - GraphQL API Wrapper
- `ctl_commands.py`: High-level API functions (pods, endpoints, templates, users)
- `graphql.py`: Core GraphQL query execution engine
- `mutations/`: GraphQL mutations (create/update/delete operations)
- `queries/`: GraphQL queries (read operations)

#### `/runpod/serverless/` - Worker SDK
- `worker.py`: Main worker orchestration and job processing loop
- `modules/rp_handler.py`: Request/response handling for serverless functions
- `modules/rp_fastapi.py`: Local development server (FastAPI-based)
- `modules/rp_scale.py`: Auto-scaling and concurrency management
- `modules/rp_ping.py`: Health monitoring and heartbeat system

#### `/runpod/cli/` - Command Line Interface
- `entry.py`: Main CLI entry point using Click framework
- `groups/`: Modular command groups (config, pod, project, ssh, exec)
- Uses Click framework with rich terminal output and progress bars

#### `/runpod/endpoint/` - Client SDK
- `runner.py`: Synchronous endpoint interaction
- `asyncio/asyncio_runner.py`: Asynchronous endpoint interaction  
- Supports both sync and async programming patterns

### Async/Sync Duality Pattern
The codebase maintains both synchronous and asynchronous interfaces throughout:
- Endpoint clients: `endpoint.run()` (async) vs `endpoint.run_sync()` (sync)
- Worker processing: Async job handling with sync compatibility
- HTTP clients: aiohttp for async, requests for sync operations

## Testing Requirements

### Test Coverage Standards
- **Minimum coverage**: 90% (enforced by pytest.ini configuration)
- **Test timeout**: 120 seconds per test (configured in pytest.ini)
- **Test structure**: Mirrors source code organization exactly
- **Async mode**: Auto-enabled via pytest.ini for seamless async testing
- **Coverage configuration**: Defined in pyproject.toml with omit patterns

### Local Serverless Testing
The project includes sophisticated local testing capabilities:
- `tests/test_serverless/local_sim/`: Mock Runpod environment
- Local development server via `python worker.py --rp_serve_api`
- Integration testing with worker state simulation

### Async Testing
- Uses `pytest-asyncio` for async test support
- `asynctest` for advanced async mocking
- Comprehensive coverage of both sync and async code paths

## Development Patterns

### Worker Development Workflow
```python
# Basic serverless worker pattern
import runpod

def handler_function(job):
    job_input = job["input"]
    # Process input...
    return {"output": result}

# Start worker (production)
runpod.serverless.start({"handler": handler_function})

# Local testing
# python worker.py --rp_serve_api
```

### API Usage Pattern
```python
import runpod

# Set API key
runpod.api_key = "your_api_key"

# Async endpoint usage
endpoint = runpod.Endpoint("ENDPOINT_ID")
run_request = endpoint.run({"input": "data"})
result = run_request.output()  # Blocks until complete

# Sync endpoint usage  
result = endpoint.run_sync({"input": "data"})
```

### Error Handling Architecture
- Custom exceptions in `runpod/error.py`
- GraphQL error handling in API wrapper
- Worker error handling with job state management
- HTTP client error handling with retry logic (aiohttp-retry)

## CI/CD Pipeline

### GitHub Actions Workflows
- **CI-pytests.yml**: Unit tests across Python 3.8, 3.9, 3.10.15, 3.11.10 matrix using uv
- **CI-e2e.yml**: End-to-end integration testing  
- **CI-codeql.yml**: Security analysis
- **CD-publish_to_pypi.yml**: Production PyPI releases with release-please automation
- **CD-test_publish_to_pypi.yml**: Test PyPI releases
- **vhs.yml**: VHS demo recording workflow
- **Manual workflow dispatch**: Available for force publishing without release-please

### Version Management
- Uses `setuptools_scm` for automatic versioning from git tags
- No manual version updates required in source code
- Version file generated at `runpod/_version.py`
- **Release-please automation**: Automated releases based on conventional commits
- **Worker notification**: Automatically notifies runpod-workers repositories on release

## Key Dependencies

### Production Dependencies (requirements.txt)
- `aiohttp[speedups]`: Async HTTP client (primary)
- `fastapi[all]`: Local development server and API framework
- `click`: CLI framework  
- `boto3`: AWS S3 integration for file operations
- `paramiko`: SSH client functionality
- `requests`: Sync HTTP client (fallback/compatibility)

### Development Dependencies (pyproject.toml dependency-groups)
- **test group**: `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-timeout`, `faker`, `nest_asyncio`
- **dev group**: `build`, `twine` for package building and publishing
- **Lock file**: `uv.lock` provides deterministic dependency resolution across environments
- **Dynamic dependencies**: Production deps loaded from `requirements.txt` via pyproject.toml

## Build System Configuration

### pyproject.toml as Primary Configuration
- **Project metadata**: Name, version, description, authors defined in pyproject.toml
- **Build system**: Uses setuptools with setuptools_scm backend
- **Dependency management**: Hybrid approach with requirements.txt for production deps
- **CLI entry points**: Defined in `[project.scripts]` section
- **Tool configurations**: pytest coverage settings, setuptools_scm configuration

### Legacy Compatibility
- **setup.py**: Maintained for backward compatibility but not primary configuration
- **requirements.txt**: Still used for production dependencies, loaded dynamically
- **Version management**: Automated via setuptools_scm, no manual updates needed

## Project-Specific Conventions

### GraphQL Integration
- All Runpod API interactions use GraphQL exclusively
- Mutations and queries are separated into distinct modules
- GraphQL client handles authentication and error responses

### CLI Design Philosophy  
- Modular command groups using Click
- Rich terminal output with progress indicators
- Configuration wizard for user onboarding
- SSH integration for pod access

### Serverless Worker Architecture
- Auto-scaling based on job queue depth
- Health monitoring with configurable intervals
- Structured logging throughout worker lifecycle
- Local development server mirrors production environment

### File Organization Principles
- Source code mirrors API/functional boundaries
- Tests mirror source structure exactly
- Clear separation between API wrapper and worker SDK
- CLI commands grouped by functional area

## Testing Strategy Notes

When working with this codebase:
- Always run full test suite before major changes (`uv run pytest`)
- Use local worker testing for serverless development (`--rp_serve_api` flag)
- Integration tests require proper mocking of Runpod API responses
- Async tests require careful setup of event loops and timeouts
- **Lock file usage**: `uv.lock` ensures reproducible test environments
- **CI/CD integration**: Tests run automatically on PR with uv for consistent results

## Modern Development Workflow

### Key Improvements
- **uv adoption**: Faster dependency resolution and installation
- **Lock file management**: `uv.lock` ensures deterministic builds across environments
- **Release automation**: release-please handles versioning and changelog generation
- **Worker ecosystem**: Automated notifications to dependent worker repositories
- **Manual override**: Workflow dispatch allows manual publishing when needed
- **Enhanced CI**: Python version matrix testing with uv for improved reliability