#!/usr/bin/env python3
"""Shared secret-redaction primitives. Imported by both pipeline stages.

Two hard-won design rules live here; both came out of red-teaming and both look
like over-engineering until you see the attack.

RULE 1 — no unbounded upper bound, no bare \\b.
    A pattern like `rpa_[A-Za-z0-9]{20,80}\\b` fails TOTALLY when the bound is
    exceeded: an 81-character key produces NO match, not a partial one, and
    sails through unredacted. Same for `AIza[...]{35}` against 36 chars, and
    `gh[pousr]_[...]{36,255}` against 256. Every bound here is open-ended, and
    `\\b` is replaced with an explicit negative lookbehind so that
    `Bearerrpa_LIVEKEY` is still caught.

RULE 2 — PEM blocks are found by line scan, never by regex.
    The natural regex, `BEGIN ... .*? ... END`, is quadratic. Feed it a diff of
    repeated `-----BEGIN PRIVATE KEY-----` lines with no END and every start
    position scans to EOF. Measured on this exact code: 1.8s at 120KB, 30s at
    500KB, 66s at 750KB — clean 4x per doubling, so ~4MB exceeds a 15-minute job
    timeout. That is a free CI outage on every push, triggered by one plausible
    "vendored test fixture". The line scan below is linear and cannot backtrack.
"""

from __future__ import annotations

import re

# Below this length, replacing every occurrence does more harm than good: a
# 6-character "secret" is usually a false positive and blanking it everywhere
# shreds the surrounding diff.
MIN_REDACT_LEN = 8

# Hard ceiling on redaction input. Bounds the worst case of every pattern below
# regardless of what the attacker sends.
MAX_REDACT_CHARS = 400_000

# Fallback patterns, applied on top of any exact-match values supplied by a
# scanner. These are the last line of defence for credentials the scanner scored
# below threshold or has no rule for.
#
# All are prefix + single-character-class runs. The class never contains the
# character that follows it, so the greedy run is unambiguous and each pattern is
# linear. No nested quantifiers, no `.*`, no alternation over variable-length
# branches — keep it that way if you add to this list.
# SEVERAL PATTERNS MATCH CONTEXT AS WELL AS THE CREDENTIAL — `runpod-legacy-uuid`
# takes up to 40 preceding characters, `db-uri-password` takes the username. That
# is fine for redaction, which replaces the whole match, and a trap for anything
# that tries to JUDGE the value: an earlier caller tested `m.group(0)` and
# labelled `RUNPOD_API_KEY_latest = "<live uuid>"` a placeholder because `latest`
# contains `test`. Nothing reads these matches for their value today — the
# scanner job gets the credential from betterleaks itself. Add a named group
# around the credential before writing anything that needs one.
#
# NO leading anchors. An earlier version used `(?<![A-Za-z0-9_-])` to avoid
# matching mid-token, and that was backwards for a redactor: it made
# `hdr=Bearerrpa_LIVEKEY` fail to match at all, leaking the key. Over-matching
# costs a little diff readability; under-matching costs a credential. The
# prefixes below are distinctive enough that a bare match is the right call.
GENERIC_PATTERNS: list[tuple[str, str]] = [
    ("runpod-key", r"rpa_[A-Za-z0-9]{20,}"),
    ("aws-akid", r"(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16,}"),
    ("github-pat", r"gh[pousr]_[A-Za-z0-9]{36,}"),
    ("slack-token", r"xox[abposre]-[A-Za-z0-9-]{10,}"),
    ("openai-key", r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    ("google-key", r"AIza[0-9A-Za-z_-]{35,}"),
    ("grafana-token", r"glc_[A-Za-z0-9+/=]{20,}"),
    ("stripe-key", r"[rs]k_(?:live|test)_[0-9a-zA-Z]{20,}"),
    # Segments bounded: the class contains `-` but not `.`, so a run that fails
    # to find its `.` backtracks the whole run. Bounding caps that at O(n*1024)
    # instead of O(n^2) on input like "eyJ-eyJ-eyJ-...".
    (
        "jwt",
        r"eyJ[A-Za-z0-9_-]{10,1024}"
        r"\.[A-Za-z0-9_-]{10,1024}\.[A-Za-z0-9_-]{10,1024}",
    ),
    # Legacy Runpod keys are bare UUIDs with full API access. Indistinguishable
    # from a request ID, so gate on nearby "runpod" context. Mirrors the rule in
    # gitleaks-runpod.toml — keep both in sync.
    (
        "runpod-legacy-uuid",
        r"(?i)runpod[^\n]{0,40}([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
        r"[0-9a-f]{4}-[0-9a-f]{12})",
    ),
    # Credential classes the original list had no fallback for at all.
    # {32,} not {64,}: real Azure keys are 88 base64 chars, but a shorter blob
    # assigned to something named "accountkey" is still a credential, and a bound
    # that is too high fails totally rather than partially.
    (
        "azure-storage-key",
        r"(?i)(?:accountkey|storage[_-]?key)[\"']?\s*[:=]\s*[\"']?"
        r"([A-Za-z0-9+/]{32,}={0,2})",
    ),
    (
        "db-uri-password",
        r"(?i)(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://"
        r"[^\s:@/]{1,256}:([^\s:@/]{6,256})@",
    ),
    (
        "generic-hex-secret",
        r"(?i)(?:secret|token|password|passwd|api[_-]?key)[\"']?\s*[:=]\s*[\"']?"
        r"([0-9a-f]{32,})",
    ),
]

_COMPILED = [(name, re.compile(p)) for name, p in GENERIC_PATTERNS]

# Mirrors the scanner's own `private-key` rule: `(?i)`, and a label class that
# admits `_` and `-`. A backstop that is NARROWER than the primary is not a
# backstop. This pattern used to be case-sensitive with a `[ A-Za-z0-9]` label,
# so it missed `-----BEGIN RSA-2048 PRIVATE KEY-----` and every lowercase
# spelling — and because the scanner's rule needs a closing `KEY-----` anchor,
# an UNTERMINATED block in either of those spellings was missed by both layers
# and reached the model vendor in plaintext.
_PEM_BEGIN = re.compile(r"(?i)-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----")
_PEM_END = re.compile(r"(?i)-----END[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----")


def redact_private_keys(text: str) -> tuple[str, int]:
    """Collapse PEM private-key blocks. Line scan, deliberately not a regex.

    Handles three cases the single-regex version got wrong:
      * `PRIVATE KEY BLOCK-----` (PGP) — the old pattern required the literal
        `PRIVATE KEY-----` and never matched PGP blocks at all.
      * an unterminated block — the body is the secret, and there is no END
        marker to anchor on, so redact to end of the base64 run.
      * many BEGIN markers with no END — quadratic in the regex version.
    """
    if _PEM_BEGIN.search(text) is None:
        return text, 0

    def probes(line: str):
        """Both the raw line and the line minus one diff marker.

        Testing both is not belt-and-braces, it is required. A PEM header starts
        with '-----', and '-' is also a diff deletion marker, so any rule that
        picks ONE interpretation gets the other case wrong:
          * lstrip("+- ")  ate the header's own dashes  -> bare PEM leaked
          * strip one char ate one of the five dashes   -> bare PEM leaked
        Trying both forms handles '-----BEGIN', '+-----BEGIN' and ' -----BEGIN'
        without having to know which context we are in.
        """
        yield line
        if line[:1] in "+- ":
            yield line[1:]

    out: list[str] = []
    count = 0
    inside = False
    # `-`, `_` and `,` are in the class because an ENCRYPTED key carries RFC 1421
    # headers INSIDE the block — `Proc-Type: 4,ENCRYPTED`, `DEK-Info: DES-EDE3-CBC,…`.
    # Without them the first header line failed this test, the scan left the
    # block at the `inside = False` below, and every base64 body line after it
    # was emitted verbatim. That needed no unusual label: a plain
    # `-----BEGIN RSA PRIVATE KEY-----` with a passphrase was enough.
    # The END marker is tested first, so widening this cannot swallow the
    # terminator; over-redaction inside a private-key block is the safe error.
    body_re = re.compile(r"[A-Za-z0-9+/=:.,_\- \t]+")

    for line in text.splitlines(keepends=True):
        if not inside:
            if any(_PEM_BEGIN.search(p) for p in probes(line)):
                inside = True
                count += 1
                out.append("«REDACTED:private-key-block»\n")
            else:
                out.append(line)
            continue

        # Inside a block: swallow everything to the END marker, or to the first
        # line that is clearly not base64 body (covers the truncated case, where
        # there is no END marker to anchor on and the body IS the secret).
        if any(_PEM_END.search(p) for p in probes(line)):
            inside = False
            continue
        if not any(
            (not p.strip()) or body_re.fullmatch(p.strip()) for p in probes(line)
        ):
            inside = False
            out.append(line)

    return "".join(out), count


def redact(text: str, mapping: dict[str, str] | None = None) -> tuple[str, int]:
    """Redact exact-match values, then PEM blocks, then the fallback patterns.

    `mapping` maps a known-secret string to its placeholder; pass the values a
    scanner extracted. Callers with no scanner output pass None and rely on the
    patterns alone.
    """
    if len(text) > MAX_REDACT_CHARS:
        text = text[:MAX_REDACT_CHARS] + "\n[... truncated before redaction ...]\n"

    text, count = _redact_exact(text, mapping or {})

    text, n = redact_private_keys(text)
    count += n

    for name, pattern in _COMPILED:
        text, n = pattern.subn(f"«REDACTED:{name}»", text)
        count += n

    # Second exact pass: the pattern sweep can shift offsets and expose a value
    # that was previously inside a longer match.
    text, n = _redact_exact(text, mapping or {})
    return text, count + n


def _redact_exact(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    """Replace known secret values by merging overlapping match spans.

    Sequential `str.replace` cannot handle two secrets that OVERLAP. With
    findings "AAAABBBB" and "BBBBCCCC" over the text "AAAABBBBCCCC", replacing
    the first consumes the "BBBB" the second needs, and "CCCC" — half of a real
    credential — survives into the output. Measured, before this change.

    Collecting every span first, merging the overlaps, then rewriting once
    redacts the union, so no fragment of any known secret can remain.
    """
    if not mapping:
        return text, 0

    spans: list[tuple[int, int, str]] = []
    for value, placeholder in mapping.items():
        start = text.find(value)
        while start != -1:
            spans.append((start, start + len(value), placeholder))
            start = text.find(value, start + 1)
    if not spans:
        return text, 0

    spans.sort()
    merged: list[tuple[int, int, str]] = []
    for s, e, ph in spans:
        if merged and s <= merged[-1][1]:
            ps, pe, pph = merged[-1]
            merged[-1] = (ps, max(pe, e), pph if pe >= e else ph)
        else:
            merged.append((s, e, ph))

    out, prev = [], 0
    for s, e, ph in merged:
        out.append(text[prev:s])
        out.append(ph)
        prev = e
    out.append(text[prev:])
    return "".join(out), len(spans)


def safe_rule_name(value, limit: int = 40) -> str:
    """Sanitise a scanner-supplied rule id before it goes in a placeholder.

    Rule ids come from `gitleaks-runpod.toml`, a file a hostile PR can edit. Left
    raw, a 64KB rule id against 10k matches turned a 120KB diff into 655MB of
    output in testing — a 5,463x amplification, and a clean route to OOM in the
    next stage. A multi-line id also injects arbitrary text into the redacted
    diff.
    """
    return re.sub(r"[^A-Za-z0-9._-]", "", str(value or "secret"))[:limit] or "secret"


def build_redaction_map(findings: list) -> dict[str, str]:
    """Map raw secret values from a gitleaks report to their placeholders."""
    mapping: dict[str, str] = {}
    for f in findings:
        if not isinstance(f, dict):
            continue
        rule = safe_rule_name(f.get("RuleID"))
        for field in ("Secret", "Match"):
            value = str(f.get(field) or "").strip()
            if len(value) >= MIN_REDACT_LEN:
                mapping.setdefault(value, f"«REDACTED:{rule}»")
    return mapping


# Values whose own text says they are fake. Mirrors the allowlist in
# gitleaks-runpod.toml — keep both in sync, or the model is asked to triage
# detections the deterministic scanner deliberately never raised. `test` is
# deliberately NOT here even though it looks like it belongs: it is a substring
# of `latest`, `testing` and `contest`, it is absent from the TOML, and it would
# auto-clear a real `sk_test_` Stripe key.
PLACEHOLDERISH = re.compile(
    r"(?i)(example|dummy|placeholder|xxxx+|your[_-]?key|redacted|000000"
    r"|changeme|sample)"
)
# How much of the value the placeholder text must account for. A bare substring
# test is not enough, and believing it was is what made this a gate bypass:
# appending `-example` to a LIVE key cleared it, while the committed string still
# contained the real credential in full. A genuine placeholder is mostly
# placeholder (`rpa_EXAMPLEEXAMPLEEXAMPLE`, `xxxxxxxx`, `your-key-here`); a live
# key with a word bolted on is mostly entropy. Anything below the line is left
# unmarked, which means the model treats it as a true positive — the safe way to
# be wrong.
PLACEHOLDER_SHARE = 0.5


def looks_fake(value: str) -> bool:
    """Does the value's own text say it is fake, and mean it?

    Dominance, not substring. See PLACEHOLDER_SHARE.

    Callable ONLY where the real credential is in hand — which, in this
    pipeline, is the scanner job and nowhere else. It answers the one question
    the triage job cannot answer for itself once the value has been masked, and
    it answers it from the value itself rather than from regex context beside
    it. An earlier version derived the value by re-matching `GENERIC_PATTERNS`
    against the diff and testing `m.group(0)`, so `RUNPOD_API_KEY_latest =
    "<live uuid>"` was labelled a placeholder because `latest` contains `test`.
    """
    if not value:
        return False
    covered = sum(len(m.group(0)) for m in PLACEHOLDERISH.finditer(value))
    return covered >= len(value) * PLACEHOLDER_SHARE
