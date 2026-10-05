#!/usr/bin/env python3
"""GitHub API helpers shared by stage 2's jobs, so both agree on the PR under review.

Invariants:
- Only `workflow_run.head_sha` and `workflow_run.head_repository.full_name`
  are trusted; the PR number, file list and contents are derived from them.
- Content is fetched by blob SHA from the base repo (where fork objects live),
  so both jobs get identical bytes.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

GITHUB_API = "https://api.github.com"
HTTP_TIMEOUT_S = 60

def log(msg: str) -> None:
    print(msg, flush=True)


def request(url: str, headers: dict, data: bytes | None = None,
            raw: bool = False, method: str | None = None):
    """Minimal HTTP helper. Returns (status, body) and never raises on 4xx/5xx."""
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            body = resp.read()
            return resp.status, (body if raw else json.loads(body or b"{}"))
    except urllib.error.HTTPError as exc:
        # Never log the headers or exception repr; either can hold the token.
        return exc.code, exc.read()[:400].decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        log(f"::warning::request to {url.split('?')[0]} failed: {type(exc).__name__}")
        return 0, None


def gh_headers(token: str, accept: str = "application/vnd.github+json") -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pr-security-gate",
    }


def is_sha(value: str, exact: bool = False) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{40}" if exact else r"[0-9a-f]{7,40}",
                             value or ""))


def resolve_pr(repo: str, head_sha: str, head_repo: str, token: str) -> dict | None:
    """Find the one PR whose head is `head_sha` (from `head_repo`, if given).

    Never takes a PR number from stage 1's artifact: it is attacker-controlled
    and would aim the triage job's write token at another PR.
    """
    if not is_sha(head_sha):
        log("::error::head SHA missing or malformed; refusing to continue")
        return None

    status, body = request(
        f"{GITHUB_API}/repos/{repo}/commits/{head_sha}/pulls", gh_headers(token)
    )
    if status != 200 or not isinstance(body, list):
        log(f"::warning title=Could not resolve PR::commit lookup returned {status}")
        return None

    matches = [
        p for p in body
        if isinstance(p, dict)
        and ((p.get("head") or {}).get("sha") == head_sha)
        and (not head_repo
             or (((p.get("head") or {}).get("repo") or {}).get("full_name") == head_repo))
    ]
    if len(matches) != 1:
        log(f"::warning::expected exactly 1 PR for {head_sha[:12]}, got {len(matches)}")
        return None

    pr = matches[0]
    log(f"resolved {head_sha[:12]} to PR #{pr['number']} ({head_repo or 'same-repo'})")
    return pr


def changed_files(repo: str, pr_number: int, head_sha: str, token: str):
    """List the PR's files as {"path", "status", "blob_sha"}. Returns (files, truncated).

    Callers must treat `truncated` as a failure.
    - Uses /pulls/{n}/files: compare ignores `per_page` and stops at 300 files.
    - Completeness is checked against the PR's `changed_files`, not page shape.
    - The head SHA is checked before and after, since this endpoint follows the
      PR's current head.
    """
    status, pr = request(f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}",
                         gh_headers(token))
    if status != 200 or not isinstance(pr, dict):
        log(f"::warning::pull request lookup returned {status}")
        return [], True
    if ((pr.get("head") or {}).get("sha")) != head_sha:
        log("::warning::pull request head no longer matches the trusted SHA")
        return [], True
    expected = pr.get("changed_files")

    out: list[dict] = []
    for page in range(1, 32):          # 31 * 100 > GitHub's 3,000-file ceiling
        status, batch = request(
            f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/files"
            f"?per_page=100&page={page}",
            gh_headers(token),
        )
        if status != 200 or not isinstance(batch, list):
            log(f"::warning::file list page {page} returned {status}")
            return out, True
        for f in batch:
            if not isinstance(f, dict):
                continue
            out.append({
                "path": str(f.get("filename") or ""),
                "status": str(f.get("status") or ""),
                # Head blob SHA; empty for a deleted file.
                "blob_sha": str(f.get("sha") or ""),
            })
        if len(batch) < 100:
            break
    else:
        return out, True

    status, after = request(f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}",
                            gh_headers(token))
    if status != 200 or not isinstance(after, dict) or \
            ((after.get("head") or {}).get("sha")) != head_sha:
        log("::warning::pull request head moved while enumerating files")
        return out, True

    if not isinstance(expected, int) or len(out) != expected:
        log(f"::warning::listed {len(out)} file(s), pull request reports {expected}")
        return out, True
    return out, False


def fetch_blob(repo: str, blob_sha: str, token: str, max_bytes: int):
    """Fetch one blob by content address. Returns (bytes, reason_if_absent)."""
    if not is_sha(blob_sha, exact=True):
        return None, "no blob sha at head"
    status, body = request(
        f"{GITHUB_API}/repos/{repo}/git/blobs/{urllib.parse.quote(blob_sha)}",
        gh_headers(token, "application/vnd.github.raw"),
        raw=True,
    )
    if status != 200 or not isinstance(body, bytes):
        return None, f"blob fetch returned {status}"
    if len(body) > max_bytes:
        return None, f"blob is {len(body)} bytes, over the {max_bytes} cap"
    return body, ""
