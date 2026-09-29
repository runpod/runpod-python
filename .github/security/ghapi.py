#!/usr/bin/env python3
"""GitHub API access for the privileged stage, pinned to trusted inputs.

Split out of ai_security_review.py so the scanner job and the triage job share
ONE implementation of the trust-critical derivation. Two copies of `resolve_pr`
is two chances for them to disagree about which pull request is under review,
and the whole gate rests on that answer.

The only inputs anything here trusts are `workflow_run.head_sha` and
`workflow_run.head_repository.full_name`, both GitHub-supplied and unforgeable
by a fork. Everything else — the PR number, the base SHA, the file list, the
file contents — is derived from those two through this module.

Content is addressed by BLOB SHA rather than by path+ref. The file list hands
back the blob SHA of every changed file at the head commit, and
`git/blobs/{sha}` serves it from the base repository's object store, which is
where a fork PR's objects already live. That sidesteps the question of whether the contents API
will resolve a fork SHA in the base repo, and it makes the fetch
content-addressed: the scanner job and the triage job asking for the same blob
SHA cannot be served different bytes.
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
        # Deliberately do NOT include the request headers or the exception repr
        # in any log line — an Authorization value can end up in either.
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
    """Find the PR for a commit, using only values GitHub supplied.

    Never reads a PR number from stage 1's artifact. That number would be
    attacker-influenced, and pointing it at a third party's PR would borrow the
    triage job's `pull-requests: write` token to post there — an excellent place
    to plant a convincing "security review passed".

    `head_repo` disambiguates the case where two PRs share a head SHA (the same
    fork branch opened against two base branches). Both belong to the same author
    so there is no privilege gain, but filtering removes the ambiguity and stops
    an attacker re-triggering churn on someone else's PR.
    """
    if not is_sha(head_sha):
        log("::error::head SHA missing or malformed; refusing to continue")
        return None

    status, body = request(
        f"{GITHUB_API}/repos/{repo}/commits/{head_sha}/pulls", gh_headers(token)
    )
    if status != 200 or not isinstance(body, list):
        # "Could not verify" is not "verification failed" — say which, loudly,
        # so a broken pipeline does not read as a repelled attack.
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
    """Enumerate the PR's files. Returns (files, truncated).

    Each file is {"path", "status", "blob_sha"}. `truncated` is True whenever
    this did not see the whole change, and every caller must treat that as a
    failure: a partial file list means a partial scan, and a partial scan
    reporting clean is the failure mode this pipeline exists to prevent.

    USES /pulls/{n}/files, NOT compare. An earlier version paged
    `compare/{base}...{head}` and inferred the end of the list from a short
    page. Measured against a real 3,000-file range, compare IGNORES `per_page`,
    returns exactly 300 files on page 1 and an EMPTY page 2 — so that version
    saw a 300-file prefix, read the empty page as "no more files", and reported
    `truncated=False`. A PR large enough could have hidden a credential past
    file 300 and still gone green. `/pulls/{n}/files` honours `per_page` and
    pages properly (verified: 25/25/10 for a 60-file PR).

    Truncation is not inferred from page shape at all. It is decided by
    comparing against `changed_files` on the pull request itself, which is
    authoritative, so GitHub's 3,000-file ceiling — or any future cap — fails
    closed without this code having to know the number.

    The head SHA is checked before AND after enumerating. This endpoint tracks
    the PR's CURRENT head rather than a pinned range, so a push landing
    mid-enumeration would otherwise blend two commits' file lists; if the head
    moved, this reports truncated and the run for the new SHA does the work.
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
                # Blob SHA at HEAD. Absent for a deleted file, which is correct:
                # there is nothing at head to scan.
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
