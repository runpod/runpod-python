#!/usr/bin/env python3
"""Stage 1: strip secret material from scanner reports before they are uploaded.

Scope note — this used to also redact and upload the PR diff, and that was a
mistake worth recording so nobody reinstates it:

  * `git diff BASE HEAD` emits deleted lines, context lines and hunk-header
    function context — all BASE-branch content. gitleaks is given
    `--log-opts=BASE..HEAD`, which covers only the PR's own commits. So a PR that
    merely DELETES a pre-existing config file, or adds five lines of whitespace
    near one, pulls base-branch credentials into the diff that the redaction map
    is guaranteed to be empty for. Verified: a "whitespace tidy-up" touching four
    lines leaked an HMAC secret from three lines away, via `--unified=5` context.
  * Stage 2 consumes nothing from here at all — not the diff, not the findings,
    not the counts. It re-runs the scanner itself over blobs pinned to the
    trusted head SHA. So there is no reason for a diff to be in the artifact,
    and every reason for it not to be.

What is left is small and safe: the two scanner reports, with every field that
carries key material removed. Nothing in `artifact/` should ever again be
something stage 2 relies on for content.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from redaction import build_redaction_map, redact, safe_rule_name


def fingerprint(secret: str) -> str:
    """A stable, non-reversible handle so humans can correlate findings.

    Deliberately not a hash of the secret alone — a bare SHA of a short or
    low-entropy credential is brute-forceable, which would defeat the point of
    redacting it. Length plus first/last character distinguishes two findings in
    a review and reveals nothing usable.
    """
    if not secret:
        return ""
    return f"len={len(secret)}:{secret[0]}…{secret[-1]}"


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
                # str()-coerce before round(): a type-confused Entropy field
                # (attacker controls the TOML) would otherwise raise TypeError.
                "entropy": str(f.get("Entropy") or "")[:12],
                "fingerprint": fingerprint(secret),
            }
        )
    return out


def sanitize_semgrep(report: dict, mapping: dict[str, str]) -> dict:
    """Rebuild the semgrep report without the fields that carry source text.

    `extra.lines` holds the matched source and `extra.metavars[*].
    abstract_content` the matched fragment. Under `--config=p/secrets` those
    fields ARE the credential. We drop `extra` wholesale and keep a redacted
    excerpt.

    Note the redact-then-slice order. Slicing first was a real bug: both
    redaction channels need the whole value present — exact-match needs the full
    string, and the PEM scan needs the END marker — so truncating to 400 chars
    first let ~370 characters of live key body through into the artifact.
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
    # Carry the "never scanned" sentinel through. An empty `results` list is
    # ambiguous — it means both "clean" and "the scan did not happen" — and the
    # second one must not be reported as the first.
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
    # JSON null is how betterleaks reports "nothing found" (Go marshals a nil
    # slice as null, where gitleaks wrote []). Expected value, not a malformed
    # report — warning on it made every clean scan look broken.
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
                # null, not 0, when semgrep never ran: a reader glancing at a
                # count sees "unknown" instead of "clean".
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
