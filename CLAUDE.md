# Runpod-python - build/flash-based-e2e-tests Worktree

> This worktree inherits patterns from main. See: /Users/deanquinanola/Github/python/flash-project/runpod-python/main/CLAUDE.md

## Branch Context

**Purpose:** Replace archaic e2e test infrastructure (CI-e2e.yml + mock-worker + runpod-test-runner) with flash-based e2e tests that validate real SDK behaviors through `flash run` dev server.

**Status:** Implementation complete, pending PR review

**Dependencies:** runpod-flash (PyPI)

## Architecture

- `tests/e2e/fixtures/all_in_one/` - Flash project with QB and LB handler fixtures
- `tests/e2e/conftest.py` - Session-scoped flash server lifecycle (port 8100, SIGINT cleanup)
- `tests/e2e/test_*.py` - 7 test files covering sync/async handlers, state persistence, SDK endpoint client, async SDK client, cold start, LB dispatch
- `.github/workflows/CI-e2e.yml` - PR workflow (QB + cold_start, requires RUNPOD_API_KEY)
- `.github/workflows/CI-e2e-nightly.yml` - Full suite including LB tests

## Key Discovery: QB Routes Dispatch Remotely

`@Endpoint(name=..., cpu=...)` wraps functions with `@remote`, which provisions real serverless endpoints even in `flash run` dev mode. This means ALL tests (QB and LB) require `RUNPOD_API_KEY`. There is no truly local-only execution mode through flash's QB routes.

## Running Tests

```bash
# Install dependencies
uv venv --python 3.12 && source .venv/bin/activate
uv pip install runpod-flash pytest pytest-asyncio pytest-timeout httpx
uv pip install -e . --force-reinstall --no-deps

# Run QB + cold_start tests (requires RUNPOD_API_KEY for QB, cold_start is local)
RUNPOD_API_KEY=... pytest tests/e2e/ -v -m "qb or cold_start" -p no:xdist --timeout=600 -o "addopts="

# Run all tests including LB
RUNPOD_API_KEY=... pytest tests/e2e/ -v -p no:xdist --timeout=600 -o "addopts="
```

## Request Format

Flash maps `input` dict fields to handler function kwargs. For `sync_handler(input_data: dict)`:
```json
{"input": {"input_data": {"prompt": "hello"}}}
```

## Next Steps

- [ ] Create PR against main
- [ ] Verify CI passes with RUNPOD_API_KEY secret configured

---

For shared development patterns, see main worktree CLAUDE.md.
