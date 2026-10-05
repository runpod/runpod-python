#!/usr/bin/env python3
"""Scan the PR's head files with betterleaks from the default-branch workflow.

Stage 1 runs from the PR's own ref, so its scanner, config and report are
author-controlled; this job's are not.

Invariants:
- Only `head_sha` and `head_repository` from the workflow_run event are trusted.
- Content is fetched by blob SHA.
- No Runpod key or write token: this runs a binary over attacker-supplied bytes.
- PR-supplied `.gitignore`/`.gitattributes` cannot hide or alter files in the
  scan commit, which is checked against the head blob SHAs.
- The findings file carries locations and rules, never `Secret` or `Match`.
- The raw report is deleted after reading.
- Any error exits non-zero with no findings file: a crashed scan is not a pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

from ghapi import changed_files, fetch_blob, log, resolve_pr
from redaction import looks_fake, safe_rule_name

# Per-file download and scan cap, matching --max-target-megabytes.
MAX_BLOB_BYTES = 25 * 1024 * 1024
# Files past this cap are reported as skipped, which fails the gate.
MAX_FILES = 3000

# Scanner ignore files. Never written into the tree; reported so the triage
# job fails closed on them.
SUPPRESSION_NAMES = {".gitleaksignore", ".betterleaksignore", ".leaksignore"}

SKIP_STATUSES = {"removed"}

# betterleaks' archive/inner-path separator, e.g. `bundle.zip!inner.txt`.
INNER_PATH_SEPARATOR = "!"


def safe_relpath(path: str) -> str | None:
    """Return an attacker-authored path as a relative path inside the tree, or
    None if it could escape the tree or touch `.git`."""
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
    """Write each changed file's head blob into `work`.

    Returns (written, skipped, suppression); any skipped file fails the gate.
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
    """Commit the tree once so `betterleaks git` scans exactly the head state.

    Returns {path: committed object id}.
    """
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")

    def run(*args):
        return subprocess.run(args, cwd=work, env=env, check=True,
                              capture_output=True, text=True)

    run("git", "init", "-q", "-b", "scan")
    # A PR-supplied `.gitattributes` could make `git log -p` show files as
    # binary (unscanned) or transcode them. `info/attributes` outranks every
    # in-tree attributes file.
    os.makedirs(os.path.join(work, ".git", "info"), exist_ok=True)
    with open(os.path.join(work, ".git", "info", "attributes"), "w",
              encoding="utf-8") as fh:
        fh.write("* diff -text !working-tree-encoding !filter\n")
    # No pathspec, so no filename is parsed as a flag; -f so a PR-supplied
    # `.gitignore` cannot drop files from the commit.
    run("git", "add", "-A", "-f")
    run("git", "-c", "user.name=secret-gate", "-c", "user.email=noreply@github.com",
        "-c", "commit.gpgsign=false",
        "commit", "-q", "--allow-empty", "-m", "head state under review")
    # `-s` adds the staged object id, which `main` checks against the blob SHA.
    tracked = {}
    for entry in run("git", "ls-files", "-s", "-z").stdout.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        tracked[path] = meta.split()[1]
    return tracked


def run_betterleaks(work: str, config: str, report: str) -> None:
    """Run `betterleaks git .` in `work` with stage 1's flags. Raises on scanner error.

    Running inside the tree keeps report paths repo-relative, so `config` and
    `report` must be absolute.
    """
    # Empty per-run dir; the ignore-path flag otherwise defaults to the scan target.
    noignore = tempfile.mkdtemp(prefix="bl-noignore-")
    proc = subprocess.run(
        [
            "betterleaks", "git", ".",
            f"--config={config}",
            f"--gitleaks-ignore-path={noignore}",
            "--ignore-gitleaks-allow",
            "--report-format=json",
            f"--report-path={report}",
            # Findings exit 0, so any non-zero exit is a scanner error.
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
    """Parse the report; only a missing, empty or `null` report is zero findings."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    # betterleaks writes `null` when it finds nothing.
    if data is None:
        return []
    if not isinstance(data, list):
        raise RuntimeError(f"report was {type(data).__name__}, expected a list")
    return data


def report_path(raw_path: str, work: str) -> str:
    """Strip a `work/` or `./` prefix so the scanner's `File` matches the PR file list."""
    path = raw_path
    for prefix in (work.rstrip("/") + "/", "./"):
        if path.startswith(prefix):
            path = path[len(prefix):]
    return path


def to_finding(raw: dict, blob_by_path: dict, work: str) -> dict | None:
    """Reduce a detection to its location, rules and hints, or None if unmapped.

    `Secret` and `Match` are never carried forward; the column span lets the
    triage job mask the value without seeing it.
    """
    path = report_path(str(raw.get("File") or ""), work)
    inner = ""
    if path not in blob_by_path and INNER_PATH_SEPARATOR in path:
        # A finding inside an archive maps to the archive's blob. The exact path
        # is tried first because `!` is legal in a filename.
        path, _, inner = path.partition(INNER_PATH_SEPARATOR)
    blob_sha = blob_by_path.get(path)
    if not blob_sha:
        # The caller records this as skipped (failing the gate); raising would
        # lose every other finding.
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
        "inner_path": inner[:300],     # set only for a finding inside an archive
        # Decided here for every consumer: archive (no source window), path
        # (path-only rule, no line) or line.
        "location_kind": ("archive" if inner
                          else "path" if as_int(raw.get("StartLine")) < 1
                          else "line"),
        "blob_sha": blob_sha,
        "start_line": as_int(raw.get("StartLine")),
        "end_line": as_int(raw.get("EndLine")),
        "start_column": as_int(raw.get("StartColumn")),
        "end_column": as_int(raw.get("EndColumn")),
        "rule": safe_rule_name(raw.get("RuleID"), 80),
        "rules": [safe_rule_name(raw.get("RuleID"), 80)],  # grown by merge_findings
        "description": str(raw.get("Description") or "")[:300],
        "entropy": str(raw.get("Entropy") or "")[:12],
        # Only this job sees the plaintext, so only it can judge this.
        "placeholder_shaped": looks_fake(secret),
        "secret_len": len(secret),
    }


def merge_findings(raw: list, blob_by_path: dict,
                   work: str) -> tuple[list[dict], list[dict]]:
    """Collapse detections to one finding per (file, inner_path, span), merging rules.

    Returns (findings, unmappable). The key uses the path, not the
    content-addressed blob SHA, so each path holding the same bytes gets its own
    finding and verdict.
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

    return list(by_span.values()), unmappable


def main() -> int:
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    head_sha = os.environ.get("TRUSTED_HEAD_SHA", "").strip()
    head_repo = os.environ.get("TRUSTED_HEAD_REPO", "").strip()
    out_path = os.environ["FINDINGS_PATH"]
    work = os.environ.get("SCAN_WORKDIR") or tempfile.mkdtemp(prefix="head-tree-")
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
    # GitHub's blob SHA is the git object id, so a mismatch means a file is
    # missing from, or altered in, the scan commit.
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

    # The raw report holds plaintext secrets.
    try:
        os.remove(report_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log(f"::warning::could not remove the raw report ({type(exc).__name__})")

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
        # No findings file on error; the triage job treats that as a failure.
        log(f"::error title=Head scan failed::{type(exc).__name__}: {exc}")
        sys.exit(1)
