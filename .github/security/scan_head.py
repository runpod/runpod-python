#!/usr/bin/env python3
"""Scan the pull request's head files with betterleaks, from the TRUSTED stage.

WHY THIS EXISTS. Stage 1 also runs betterleaks, and its findings cannot be
used. Under `pull_request` GitHub runs the workflow definition from the PR's own
merge ref, so a hostile author owns stage 1's YAML, its scanner config and every
byte it uploads — `count=0` next to a live key would skip the triage call and
green the gate. This module runs the same scanner from `pr-ai-review.yml`, which
`workflow_run` always executes from the default branch. The author still
controls the bytes being scanned, which is the point; they no longer control the
scanner, its config, or the report.

WHAT IT DOES NOT HOLD. No Runpod key and no write token — see the job boundary
in the workflow. It runs a binary over attacker-supplied bytes, so it is kept
credential-free and its only output is the findings file below.

NO SECRET VALUES LEAVE THIS JOB. betterleaks reports `Secret` and `Match`; both
are dropped here. What crosses to the triage job is the location (blob SHA,
line, column span), the rule that fired, and one boolean — whether the value's
own text says it is a placeholder, which is a question only this job can answer
because only this job has the value. The triage job re-reads the same blob by
content address and masks exactly the reported span, so the credential reaches
neither the artifact nor the model vendor.

WHY A SYNTHETIC REPOSITORY. The head files are written into a scratch directory
and committed once, so the scan runs through `betterleaks git` with the same
verb, the same flags and the same config as stage 1. Scanning a loose directory
instead would depend on a different subcommand behaving identically, and a gate
is the wrong place to assume that.

Every failure path exits non-zero and writes no findings file. The triage job
treats a missing or unreadable file as a failure, so a crash here fails the
merge closed rather than reporting an empty scan as clean.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from ghapi import changed_files, fetch_blob, log, resolve_pr
from redaction import looks_fake, safe_rule_name

# Per-file ceiling on what is downloaded and scanned. Stage 1 passes
# --max-target-megabytes=25 for the same reason; this bounds the download too.
MAX_BLOB_BYTES = 25 * 1024 * 1024
# Ceiling on how many changed files are materialised. A PR over this has not
# been scanned in full, which the triage job turns into a red status.
MAX_FILES = 3000

# Suppression channels live OUTSIDE .github/, so CODEOWNERS review never fires
# on them, and betterleaks reads a `.gitleaksignore` from the scan target
# regardless of --gitleaks-ignore-path. Stage 1 rejects these too, but that
# check is in a file the PR can edit, so it is not a control — this one is. The
# files are never written into the scratch tree, and their presence is reported
# so the triage job can fail closed on it.
SUPPRESSION_NAMES = {".gitleaksignore", ".betterleaksignore", ".leaksignore"}

# Deleted at head: nothing to scan. Every other status leaves a blob behind.
SKIP_STATUSES = {"removed"}

# betterleaks joins an archive to a path inside it with this (its
# `sources.InnerPathSeparator`), e.g. `bundle.zip!inner.txt`.
INNER_PATH_SEPARATOR = "!"


def safe_relpath(path: str) -> str | None:
    """Reject any path that would escape, or reach into, the scratch tree.

    The path comes from GitHub's compare response, so it is a real path in a
    real tree rather than a free-form string — but it descends from
    attacker-authored commits, and this function is what stands between it and
    `open(..., "w")`.
    """
    if not path or "\x00" in path or path.startswith("/"):
        return None
    parts = []
    for part in path.split("/"):
        if part in ("", ".", "..", ".git"):
            return None
        if any(ord(c) < 32 for c in part):
            return None
        parts.append(part)
    return os.path.join(*parts) if parts else None


def materialise(repo: str, token: str, files: list, work: str):
    """Write each changed file's head blob into the scratch tree.

    Returns (written, skipped, suppression). `skipped` names files that could
    not be fetched or were over the size cap; the triage job fails closed on a
    non-empty list, because a file this job did not scan is a file nothing
    scanned.
    """
    written, skipped, suppression = [], [], []

    for entry in files[:MAX_FILES]:
        path = entry["path"]
        if os.path.basename(path) in SUPPRESSION_NAMES:
            suppression.append(path)
            continue
        if entry["status"] in SKIP_STATUSES:
            continue

        rel = safe_relpath(path)
        if rel is None:
            skipped.append({"path": path[:300], "reason": "unsafe path"})
            continue

        blob, reason = fetch_blob(repo, entry["blob_sha"], token, MAX_BLOB_BYTES)
        if blob is None:
            skipped.append({"path": path[:300], "reason": reason})
            continue

        dest = os.path.join(work, rel)
        os.makedirs(os.path.dirname(dest) or work, exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(blob)
        written.append({"path": path, "rel": rel, "blob_sha": entry["blob_sha"]})

    if len(files) > MAX_FILES:
        skipped.append({"path": f"{len(files) - MAX_FILES} further file(s)",
                        "reason": f"over the {MAX_FILES}-file cap"})
    return written, skipped, suppression


def commit_tree(work: str) -> dict[str, str]:
    """One commit, so `betterleaks git` sees exactly the head state.

    Returns {path: the object id git committed}, so the caller can check that
    what git stored is byte-identical to what GitHub served.
    """
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")

    def run(*args):
        return subprocess.run(args, cwd=work, env=env, check=True,
                              capture_output=True, text=True)

    run("git", "init", "-q", "-b", "scan")
    # GIT MUST NOT TAKE INSTRUCTIONS FROM THE TREE IT IS SCANNING.
    #
    # `.gitattributes` is an ordinary changed file, so `materialise` writes it
    # into this tree like any other — and attributes change how git PRESENTS
    # content. `betterleaks git` reads the tree through `git log -p`, so a
    # PR-supplied `* -diff` makes every patch read "Binary files differ": the
    # scanner reports `scanned ~0 bytes`, finds nothing, and the gate goes
    # green over live credentials. `working-tree-encoding` is the same hole
    # with a different lever — it either transcodes the file or fails
    # `git add` outright, wedging the job on a red status.
    #
    # `$GIT_DIR/info/attributes` outranks any in-tree `.gitattributes`, at any
    # depth, so this one line closes the whole attribute surface. Blocklisting
    # filenames instead would just wait for the next attribute file — which is
    # the mistake the `-f` below is already making up for.
    os.makedirs(os.path.join(work, ".git", "info"), exist_ok=True)
    with open(os.path.join(work, ".git", "info", "attributes"), "w",
              encoding="utf-8") as fh:
        fh.write("* diff -text !working-tree-encoding !filter\n")
    # -A with no pathspec, so a filename that looks like a flag cannot be one.
    #
    # -f because a `.gitignore` is an ordinary changed file: `materialise`
    # writes it into this tree like any other, and `git add` without -f then
    # OBEYS it. That was a complete gate bypass needing no crafted exclusion —
    # this repo's own .gitignore already lists `.env*` and `.vault*`, so a PR
    # that touched .gitignore for any reason had those paths dropped from the
    # commit, and `betterleaks git` reported a clean scan over what was left.
    # A nested `files/.gitignore` containing `*` ignores itself and needs no
    # change to the root file at all.
    run("git", "add", "-A", "-f")
    run("git", "-c", "user.name=secret-gate", "-c", "user.email=noreply@github.com",
        "-c", "commit.gpgsign=false",
        "commit", "-q", "--allow-empty", "-m", "head state under review")
    # `-s` for the staged object id, not just the name. See `main` for why the
    # name alone is not the invariant worth checking.
    tracked = {}
    for entry in run("git", "ls-files", "-s", "-z").stdout.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        tracked[path] = meta.split()[1]
    return tracked


def run_betterleaks(work: str, config: str, report: str) -> None:
    """Same verb, same flags, same config as stage 1. Raises on scanner error.

    Invoked as `betterleaks git .` from inside the tree, exactly as stage 1
    invokes it, so the `File` paths in the report come back in the same
    spelling. Every other path passed in is absolute for the same reason.
    """
    os.makedirs("/tmp/bl-noignore", exist_ok=True)
    proc = subprocess.run(
        [
            "betterleaks", "git", ".",
            f"--config={config}",
            # The flag takes a path to an ignore file OR a folder containing
            # one, and defaults to the scan target. Aimed at an empty directory
            # there is nothing for it to find.
            "--gitleaks-ignore-path=/tmp/bl-noignore",
            "--ignore-gitleaks-allow",
            "--report-format=json",
            f"--report-path={report}",
            # Findings exit 0 so they can be told apart from a scanner error.
            "--exit-code=0",
            "--max-target-megabytes=25",
            "--no-banner",
        ],
        cwd=work, capture_output=True, text=True,
    )
    sys.stderr.write(proc.stderr[-4000:])
    if proc.returncode != 0:
        raise RuntimeError(f"betterleaks exited {proc.returncode}")


def load_report(path: str) -> list:
    """Read the report, treating only genuine emptiness as zero findings."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    # betterleaks writes JSON `null` when it finds nothing (Go marshals a nil
    # slice as null); gitleaks wrote []. Anything else is a shape we do not
    # understand, and guessing at it would be guessing at the gate.
    if data is None:
        return []
    if not isinstance(data, list):
        raise RuntimeError(f"report was {type(data).__name__}, expected a list")
    return data


def report_path(raw_path: str, work: str) -> str:
    """Normalise the scanner's `File` back to the path the compare API gave us.

    Belt and braces. The scan runs from inside the tree so the report should
    already be repo-relative, but if a future version ever reports `./x` or an
    absolute path, every lookup below would miss and the job would go red on
    every PR that has a finding. Fail-closed is right; failing closed on
    everything is not.
    """
    path = raw_path
    for prefix in (work.rstrip("/") + "/", "./"):
        if path.startswith(prefix):
            path = path[len(prefix):]
    return path


def to_finding(raw: dict, blob_by_path: dict, work: str) -> dict | None:
    """Strip the report down to a location plus a verdict-relevant hint.

    `Secret` and `Match` are read here and deliberately not carried forward. The
    column span is, because it is how the triage job masks the exact characters
    without ever seeing them.
    """
    path = report_path(str(raw.get("File") or ""), work)
    inner = ""
    if path not in blob_by_path and INNER_PATH_SEPARATOR in path:
        # betterleaks extracts archives by default (--max-archive-depth=8) and
        # reports a finding inside one as `bundle.zip!inner.txt`, using its own
        # InnerPathSeparator. Only the archive itself was ever materialised, so
        # the inner spelling is not in the map; attribute the finding to the
        # containing archive and keep the inner path for display.
        #
        # The exact path is tried FIRST, because `!` is legal in a filename and
        # splitting unconditionally would unmap a file genuinely called that.
        path, _, inner = path.partition(INNER_PATH_SEPARATOR)
    blob_sha = blob_by_path.get(path)
    if not blob_sha:
        # Deliberately NOT raising. A detection must never be dropped silently,
        # but raising here killed the job, which wrote no findings file, which
        # discarded every OTHER finding in the scan along with this one. The
        # caller records it as a skipped file instead, and `scan_blocked()`
        # already turns a non-empty `files_skipped` into a red gate with a
        # reason a maintainer can act on.
        return None

    def as_int(value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    secret = str(raw.get("Secret") or "")
    return {
        "id": 0,                       # assigned after de-duplication
        "file": path[:300],
        # Non-empty only for a finding inside a committed archive. The blob is
        # the binary container, so this is the only locator that means anything.
        "inner_path": inner[:300],
        # How this finding can be located, decided here so that every consumer
        # agrees. Three independently-derived answers is how a path-only rule
        # came to be published to a human reviewer as "line 0".
        #   archive - inside a committed archive; no source window exists
        #   path    - a path-only rule, which reports no line at all
        #   line    - the normal case
        "location_kind": ("archive" if inner
                          else "path" if as_int(raw.get("StartLine")) < 1
                          else "line"),
        "blob_sha": blob_sha,
        "start_line": as_int(raw.get("StartLine")),
        "end_line": as_int(raw.get("EndLine")),
        "start_column": as_int(raw.get("StartColumn")),
        "end_column": as_int(raw.get("EndColumn")),
        "rule": safe_rule_name(raw.get("RuleID"), 80),
        # Every rule that matched this exact span, merged below. Two of the
        # Runpod rules match any `rpa_` key, so without merging the model is
        # asked about the same 44 characters twice and billed for both.
        "rules": [safe_rule_name(raw.get("RuleID"), 80)],
        "description": str(raw.get("Description") or "")[:300],
        "entropy": str(raw.get("Entropy") or "")[:12],
        # The one judgement only this job can make: it is the only place the
        # credential exists in plaintext.
        "placeholder_shaped": looks_fake(secret),
        "secret_len": len(secret),
    }


def merge_findings(raw: list, blob_by_path: dict,
                   work: str) -> tuple[list[dict], list[dict]]:
    """Reduce raw scanner detections to one finding per (path, span).

    Returns (findings, unmappable). One detection per SPAN, not per rule:
    `runpod-api-key` and `runpod-key-in-allowlisted-path` both match every rpa_
    key, so the same characters arrived as two findings — two evidence blocks,
    two verdicts, twice the tokens, and a model invited to disagree with itself
    about one value. Merging is free recall: the span is unchanged, only its
    rule list grows.

    The key carries the FILE, not just the span. It used to key on `blob_sha`,
    which is content-addressed — so two different paths holding identical bytes
    produced one finding and only the first-reported path was ever named. Put
    the same key in `tests/fixtures/example_key.yml` and in a real config, and
    the fixture's "only a test fixture reads this" verdict cleared both. The
    inner path is in the key for the same reason: two files inside one archive
    share a blob and can share a span.
    """
    unmappable: list[dict] = []
    by_span: dict[tuple, dict] = {}

    for item in raw:
        if not isinstance(item, dict):
            raise RuntimeError(f"report entry was {type(item).__name__}, expected an object")
        f = to_finding(item, blob_by_path, work)
        if f is None:
            unmappable.append({
                "path": report_path(str(item.get("File") or ""), work)[:300],
                "reason": "the scanner reported a path with no materialised blob",
            })
            continue

        key = (f["file"], f["inner_path"], f["start_line"], f["end_line"],
               f["start_column"], f["end_column"])
        prev = by_span.get(key)
        if prev is not None:
            if f["rule"] not in prev["rules"]:
                prev["rules"].append(f["rule"])
            prev["placeholder_shaped"] = (prev["placeholder_shaped"]
                                          or f["placeholder_shaped"])
            continue
        f["id"] = len(by_span) + 1
        by_span[key] = f

    # dicts are insertion-ordered, so this is the arrival order ids were
    # assigned in.
    return list(by_span.values()), unmappable


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    head_sha = os.environ.get("TRUSTED_HEAD_SHA", "").strip()
    head_repo = os.environ.get("TRUSTED_HEAD_REPO", "").strip()
    out_path = os.environ["FINDINGS_PATH"]
    work = os.environ.get("SCAN_WORKDIR", "/tmp/head-tree")
    config = os.environ.get(
        "BETTERLEAKS_CONFIG", ".github/security/gitleaks-runpod.toml"
    )
    config = os.path.abspath(config)

    pr = resolve_pr(repo, head_sha, head_repo, token)
    if pr is None:
        log("::error title=Scan aborted::could not resolve the commit to a PR")
        return 1
    files, truncated = changed_files(repo, int(pr["number"]), head_sha, token)
    log(f"{len(files)} changed file(s) at {head_sha[:12]}"
        + (" (TRUNCATED)" if truncated else ""))

    os.makedirs(work, exist_ok=True)
    written, skipped, suppression = materialise(repo, token, files, work)
    log(f"materialised {len(written)} file(s), skipped {len(skipped)}")

    tracked = commit_tree(work)
    # Defence in depth behind the two controls in `commit_tree`, and the check
    # nothing used to do: `files_scanned` counts what was WRITTEN, which is why
    # the `.gitignore` bypass left no trace anywhere.
    #
    # The invariant is not "every file is present" but "what git committed is
    # byte-identical to what GitHub served". GitHub's blob SHA *is* the git
    # object id, so comparing them catches a file dropped from the commit AND
    # one altered on the way in by an attribute, a filter or an eol conversion
    # — the class above, checked rather than assumed.
    altered = [f["rel"] for f in written if tracked.get(f["rel"]) != f["blob_sha"]]
    for rel in altered[:20]:
        skipped.append({"path": rel[:300],
                        "reason": "not committed byte-identically to the head blob"})
    if altered:
        log(f"::error title=Files not scanned::{len(altered)} materialised file(s) "
            "did not reach the scan commit byte-identically")
    report_path = os.path.join(os.path.dirname(out_path) or ".", "betterleaks.raw.json")
    run_betterleaks(work, config, report_path)

    blob_by_path = {f["path"]: f["blob_sha"] for f in written}
    raw = load_report(report_path)
    findings, unmappable = merge_findings(raw, blob_by_path, work)
    skipped.extend(unmappable)
    merged = len(raw) - len(findings) - len(unmappable)
    if merged:
        log(f"merged {merged} duplicate detection(s) on shared spans")
    for entry in unmappable:
        log(f"::warning title=Unmappable finding::{entry['path']} — {entry['reason']}")

    # The raw report holds plaintext credentials. Nothing uploads it, but it is
    # on a runner that keeps running, so remove it rather than trusting that.
    try:
        os.remove(report_path)
    except OSError:
        pass

    payload = {
        "head_sha": head_sha,
        "pr": int(pr["number"]),
        "files_scanned": len(written),
        "files_skipped": skipped,
        "file_list_truncated": truncated,
        "suppression_files": suppression,
        "findings": findings,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    log(f"betterleaks findings: {len(findings)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        # No findings file on any error path. The triage job reads its absence
        # as a failure and publishes a red status, so a crash here blocks the
        # merge instead of reporting an unscanned PR as clean.
        log(f"::error title=Head scan failed::{type(exc).__name__}: {exc}")
        sys.exit(1)
