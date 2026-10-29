# AGENTS.md

Rules for coding agents working in this repo. They cover what reviewers have
had to ask for more than once and what the code does not make obvious. Setup,
style, and test commands are in CONTRIBUTING.md; the serverless worker design
is in ARCHITECTURE.md.

## Never edit

- `CHANGELOG.md`. release-please writes it from the squash-merge commit
  titles on `main` when it cuts a release. Write a conventional commit title
  instead (`feat(cli): ...`, `fix(apps): ...`, `perf(serverless): ...`); that
  title is the changelog entry.
- Version numbers. There is none to bump: setuptools_scm reads the version
  from the release tag release-please creates.

## Where new code goes

- **CLI.** `rp` lives in `runpod/rp_cli/`. Add a command to the group that
  already owns the resource (`rp volume`, `rp pod`, ...). Do not create a new
  top-level group for a resource that has one. If the group is still in an
  open PR, use the same file and group name so the two merge cleanly.
- **SDK.** An operation on a resource is a method on that resource's class
  (`NetworkVolume.upload()`), not a top-level function. Do not add names to
  the lazy exports in `runpod/__init__.py` for it.
- **Helpers.** A feature module holds the logic for that feature. Before
  writing a generic helper (retries, thread pools, atomic writes, pagination,
  name-or-id lookup), check what already exists and use it:
  - REST calls and pagination: `runpod/api/rest.py`
    (`run_rest_request`, `run_rest_paginated_request_async`).
  - Running async code from sync code, including inside a running loop:
    `runpod.apps.context.block`.
  - Resolving a resource by id or name: `runpod.apps.utils.lookup.find_by_id_or_name`.
  - Retries on boto3 calls: botocore's own retry config, not a hand-written
    loop.
  If nothing fits and the helper is generic, say so in the PR rather than
  growing the feature module.

## Scope

- Change only what the task needs. No drive-by refactors, renames, or docs
  edits in files the task does not touch.
- Keep a PR to one feature or fix. Unrelated changes go in their own PR.

## Reviews and CI

- Automated reviewers (Claude, Cursor, CodeQL, code-quality bots) post a lot
  of comments. Fix the real ones, but a maintainer's review decides the
  design. If a bot and a maintainer disagree, follow the maintainer.
- Do not reply to review threads, request reviews, or mark a draft ready
  unless the person you work for asks.
- `e2e` (and `validation`, which depends on it) needs the `RUNPOD_API_KEY`
  secret and fails with a 401 when that secret is missing or stale. That
  failure is not caused by your change; check `main` before chasing it.
- `pytest` enforces 90% coverage over `runpod/`. A new module needs tests
  that keep the total above it.
