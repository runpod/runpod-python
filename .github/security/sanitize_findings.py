#!/usr/bin/env python3
"""Stage 1: strip secret material from the scanner reports before upload.

The artifact is human-facing; stage 2 reads nothing from it.
- Every field carrying key material is removed.
- No diff is uploaded: its context and deleted lines are base-branch content
  the redaction map does not cover.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys

from redaction import build_redaction_map, redact, safe_rule_name


# Per-run key, never stored, so a digest cannot be brute-forced the way a bare
# hash of a short secret can.
_RUN_KEY = secrets.token_bytes(32)


def fingerprint(secret: str) -> str:
    """Keyed digest, so reviewers can correlate findings within one report."""
    if not secret:
        return ""
    return hmac.new(_RUN_KEY, secret.encode(), hashlib.sha256).hexdigest()[:12]


def sanitize_gitleaks(findings: list) -> list[dict]:
    """Keep what a reviewer needs; drop every field carrying key material."""
    out = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        secret = str(f.get("Secret") or "").strip()
        out.append(
            {
                "rule_id": safe_rule_name(f.get("RuleID"), 80),
                "description": str(f.get("Description") or "")[:300],
                "file": str(f.get("File") or "")[:300],
                "start_line": str(f.get("StartLine") or "")[:12],
                "commit": str(f.get("Commit") or "")[:12],
                "author": str(f.get("Author") or "")[:120],
                "date": str(f.get("Date") or "")[:40],
                "entropy": str(f.get("Entropy") or "")[:12],
                "fingerprint": fingerprint(secret),
            }
        )
    return out


def sanitize_semgrep(report: dict, mapping: dict[str, str]) -> dict:
    """Rebuild the semgrep report without `extra`, which holds the matched source.

    Redact before truncating: redaction needs the whole value (the PEM scan
    needs the END marker).
    """
    results = report.get("results") if isinstance(report, dict) else None
    if not isinstance(results, list):
        results = []

    out = []
    for r in results:
        if not isinstance(r, dict):
            continue
        extra = r.get("extra") if isinstance(r.get("extra"), dict) else {}
        meta = extra.get("metadata") if isinstance(extra.get("metadata"), dict) else {}
        start = r.get("start") if isinstance(r.get("start"), dict) else {}
        excerpt = redact(str(extra.get("lines") or ""), mapping)[0][:400]
        out.append(
            {
                "check_id": str(r.get("check_id") or "")[:200],
                "path": str(r.get("path") or "")[:300],
                "start_line": str(start.get("line") or "")[:12],
                "severity": str(extra.get("severity") or "")[:20],
                "shortlink": str(meta.get("shortlink") or "")[:200],
                "excerpt_redacted": excerpt,
            }
        )
    # Keep the not-scanned sentinel so an empty `results` is not read as clean.
    out_report: dict = {"results": out}
    if isinstance(report, dict) and report.get("__not_scanned"):
        out_report["__not_scanned"] = str(report["__not_scanned"])[:200]
    return out_report


def load(path: str, default, expect):
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
    except Exception as exc:  # noqa: BLE001 - any failure means "assume none"
        print(f"::warning::could not read {path} ({exc}); using default")
        return default
    # betterleaks writes `null` when it finds nothing.
    if value is None:
        return default
    if not isinstance(value, expect):
        print(f"::warning::{path} was {type(value).__name__}; using default")
        return default
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gitleaks-report", required=True)
    ap.add_argument("--semgrep", required=True)
    ap.add_argument("--out-findings", required=True)
    ap.add_argument("--out-semgrep", required=True)
    ap.add_argument("--out-meta", required=True)
    args = ap.parse_args()

    findings = load(args.gitleaks_report, [], list)
    semgrep_raw = load(args.semgrep, {}, dict)
    mapping = build_redaction_map(findings)

    with open(args.out_findings, "w", encoding="utf-8") as fh:
        json.dump(sanitize_gitleaks(findings), fh, indent=2)

    sg = sanitize_semgrep(semgrep_raw, mapping)
    with open(args.out_semgrep, "w", encoding="utf-8") as fh:
        json.dump(sg, fh, indent=2)

    try:
        pr_number = int(os.environ.get("PR_NUMBER", "0") or 0)
    except ValueError:
        pr_number = 0

    with open(args.out_meta, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "pr_number": pr_number,
                "head_sha": os.environ.get("HEAD_SHA", "")[:40],
                "gitleaks_finding_count": len(findings),
                # null, not 0, when semgrep did not run.
                "semgrep_ran": not sg.get("__not_scanned"),
                "semgrep_finding_count": (
                    None if sg.get("__not_scanned") else len(sg["results"])
                ),
                "semgrep_skip_reason": sg.get("__not_scanned") or None,
                "note": "Advisory/human-facing only. Stage 2 does not read "
                        "this artifact at all; it re-runs the scanner itself "
                        "over blobs pinned to the trusted head SHA.",
            },
            fh,
            indent=2,
        )

    semgrep_note = (
        f"semgrep NOT RUN ({sg['__not_scanned']})"
        if sg.get("__not_scanned")
        else f"{len(sg['results'])} semgrep finding(s)"
    )
    print(
        f"sanitised {len(findings)} gitleaks + {semgrep_note}; "
        f"no raw values in artifact"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
