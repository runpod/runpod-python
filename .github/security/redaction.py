#!/usr/bin/env python3
"""Secret-redaction primitives shared by both pipeline stages.

Credential runs have no upper bound: a bounded pattern does not match an
over-long key at all. PEM blocks use a linear line scan; a BEGIN..END regex is
quadratic.
"""

from __future__ import annotations

import re

# Shorter values are mostly false positives; replacing them everywhere shreds
# the diff.
MIN_REDACT_LEN = 8

# Input cap; bounds the worst case of every pattern.
MAX_REDACT_CHARS = 400_000

# Fallbacks for credentials the scanner misses. Each is a prefix plus one
# character-class run, so matching is linear; keep it that way. No leading
# anchors, so `Bearerrpa_...` still matches: under-matching leaks a key.
# Some matches include context (the `runpod` prefix, a DB username); add a named
# group before using a match as the credential value.
GENERIC_PATTERNS: list[tuple[str, str]] = [
    ("runpod-key", r"rpa_[A-Za-z0-9]{20,}"),
    ("aws-akid", r"(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16,}"),
    ("github-pat", r"gh[pousr]_[A-Za-z0-9]{36,}"),
    ("slack-token", r"xox[abposre]-[A-Za-z0-9-]{10,}"),
    ("openai-key", r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    ("google-key", r"AIza[0-9A-Za-z_-]{35,}"),
    ("grafana-token", r"glc_[A-Za-z0-9+/=]{20,}"),
    ("stripe-key", r"[rs]k_(?:live|test)_[0-9a-zA-Z]{20,}"),
    # Bounded segments cap backtracking at O(n*1024) on input like "eyJ-eyJ-...".
    (
        "jwt",
        r"eyJ[A-Za-z0-9_-]{10,1024}"
        r"\.[A-Za-z0-9_-]{10,1024}\.[A-Za-z0-9_-]{10,1024}",
    ),
    # Legacy Runpod keys are bare UUIDs, so require nearby "runpod". Keep in sync
    # with the matching rule in gitleaks-runpod.toml.
    (
        "runpod-legacy-uuid",
        r"(?i)runpod[^\n]{0,40}([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
        r"[0-9a-f]{4}-[0-9a-f]{12})",
    ),
    # {32,}: real keys are 88 chars, but anything assigned to "accountkey" counts.
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

# At least as broad as the scanner's `private-key` rule: case-insensitive, `_`
# and `-` allowed in the label. Also matches PGP `PRIVATE KEY BLOCK`.
_PEM_BEGIN = re.compile(r"(?i)-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----")
_PEM_END = re.compile(r"(?i)-----END[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----")


def redact_private_keys(text: str) -> tuple[str, int]:
    """Collapse PEM private-key blocks, terminated or not. Returns (text, count)."""
    if _PEM_BEGIN.search(text) is None:
        return text, 0

    def probes(line: str):
        """Yield the line raw and minus one diff marker; `-` also starts a header."""
        yield line
        if line[:1] in "+- ":
            yield line[1:]

    out: list[str] = []
    count = 0
    inside = False
    # Admits RFC 1421 headers in encrypted keys (`Proc-Type: 4,ENCRYPTED`).
    # END is tested first, so the wide class cannot swallow it.
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

        # Swallow to the END marker, or to the first non-body line if unterminated.
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
    """Redact `mapping` values, then PEM blocks, then GENERIC_PATTERNS.

    `mapping` maps scanner-extracted secrets to placeholders; None means patterns only.
    """
    if len(text) > MAX_REDACT_CHARS:
        text = text[:MAX_REDACT_CHARS] + "\n[... truncated before redaction ...]\n"

    text, count = _redact_exact(text, mapping or {})

    text, n = redact_private_keys(text)
    count += n

    for name, pattern in _COMPILED:
        text, n = pattern.subn(f"«REDACTED:{name}»", text)
        count += n

    # Pattern replacements can expose a value that sat inside a longer match.
    text, n = _redact_exact(text, mapping or {})
    return text, count + n


def _redact_exact(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    """Replace `mapping` values, merging overlapping spans so no fragment survives."""
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
    """Sanitise and length-cap a rule id for a placeholder.

    Rule ids come from `gitleaks-runpod.toml`, which a PR can edit; raw, they
    allow text injection and output amplification.
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


# Text marking a value as fake. Keep in sync with the gitleaks-runpod.toml
# allowlist. `test` is excluded: it matches `latest` and real `sk_test_` keys.
PLACEHOLDERISH = re.compile(
    r"(?i)(example|dummy|placeholder|xxxx+|your[_-]?key|redacted|000000"
    r"|changeme|sample)"
)
# Share of the value placeholder text must cover, so a live key with `-example`
# appended is not cleared. Below it, the value is treated as real.
PLACEHOLDER_SHARE = 0.5


def looks_fake(value: str) -> bool:
    """True if placeholder text covers at least PLACEHOLDER_SHARE of `value`.

    `value` must be the raw credential (scanner job only), not a GENERIC_PATTERNS
    match, which can include surrounding context.
    """
    if not value:
        return False
    covered = sum(len(m.group(0)) for m in PLACEHOLDERISH.finditer(value))
    return covered >= len(value) * PLACEHOLDER_SHARE
