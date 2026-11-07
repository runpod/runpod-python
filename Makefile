.PHONY: setup test test-coverage build verify quality-check

# --- Setup ---
setup:
	uv sync --group test --group dev

# --- Testing ---
test:
	uv run pytest

test-coverage:
	uv run pytest --cov=runpod --cov-branch --cov-report=term-missing --cov-report=xml --junitxml=pytest-results.xml

# --- Build ---
build:
	uv build

verify: build
	uv run twine check dist/*

# --- Quality Gate (expand with lint/format when ruff is configured) ---
quality-check: test-coverage
