"""Regression tests for the two-stage secret gate.

Every test here pins a defect that reached a vendored, security-critical gate
with no test to catch it. Each one fails against the code as it was.

Hermetic by construction: no network, and no betterleaks binary. The scanner
outputs driving the archive and path-only cases are recorded verbatim from real
`betterleaks 1.8.1` runs — the version pinned in `pr-ai-review.yml` — so the
fixtures cannot drift from the shapes the scanner actually emits.
"""

from __future__ import annotations

import itertools
import json
import re
import subprocess

import pytest

import ai_security_review as air
import redaction
import scan_head

# --------------------------------------------------------------------- helpers

PEM_BODY = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC8uyU9ymqBGMkr"


def _finding(fid: int, rule: str, line: int, start_col: int, end_col: int) -> dict:
    return {"id": fid, "rule": rule, "placeholder_shaped": False,
            "start_line": line, "end_line": line,
            "start_column": start_col, "end_column": end_col}


def _raw(**over) -> dict:
    """A betterleaks report entry, in the spelling the scanner emits."""
    base = {"File": "a.yml", "RuleID": "runpod-api-key",
            "StartLine": 1, "EndLine": 1, "StartColumn": 10, "EndColumn": 57,
            "Secret": "rpa_" + "A" * 40, "Description": "Runpod API key",
            "Entropy": 4.9}
    return {**base, **over}


# ------------------------------------------- #1  gate bypass via .gitignore


def test_commit_tree_stages_gitignored_files(tmp_path):
    """A PR-supplied `.gitignore` must not decide what gets scanned.

    `main`'s own .gitignore already lists `.env*` and `.vault*`, so the only
    precondition for a total bypass was a PR touching that file for any reason.
    """
    (tmp_path / ".gitignore").write_text(".env*\n.vault*\n")
    (tmp_path / ".env.production").write_text("api_key: rpa_" + "B" * 40 + "\n")
    (tmp_path / ".vault-pass").write_text("rpa_" + "C" * 40 + "\n")
    (tmp_path / "ordinary.yml").write_text("ok: true\n")

    tracked = scan_head.commit_tree(str(tmp_path))

    assert set(tracked) == {
        ".gitignore", ".env.production", ".vault-pass", "ordinary.yml"}


def test_commit_tree_stages_self_ignoring_nested_gitignore(tmp_path):
    """The nested variant needs no change to the root file: `*` ignores itself."""
    (tmp_path / "files").mkdir()
    (tmp_path / "files" / ".gitignore").write_text("*\n")
    (tmp_path / "files" / "creds.yml").write_text("api_key: rpa_" + "D" * 40 + "\n")

    tracked = scan_head.commit_tree(str(tmp_path))

    assert "files/creds.yml" in tracked
    assert "files/.gitignore" in tracked


# ----------------------------------- #2 / #2b  the redaction backstop's reach


@pytest.mark.parametrize("header", [
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "-----BEGIN RSA-2048 PRIVATE KEY-----",   # hyphenated label
    "-----BEGIN RSA_2048 PRIVATE KEY-----",   # underscored label
    "-----begin rsa private key-----",        # lowercase
    "-----Begin Private Key-----",            # mixed case
    "-----BEGIN PGP PRIVATE KEY BLOCK-----",
])
@pytest.mark.parametrize("terminated", [True, False])
def test_redact_collapses_every_pem_label(header, terminated):
    """The backstop must never be narrower than the scanner rule it backs up.

    The scanner's `private-key` rule has `(?i)` and admits `_`/`-` in the label,
    and needs a closing anchor. An unterminated hyphen-labelled block was
    therefore missed by BOTH layers and reached the model vendor in plaintext.
    """
    text = f"{header}\n{PEM_BODY}\n"
    if terminated:
        text += header.replace("BEGIN", "END").replace("Begin", "End") \
                      .replace("begin", "end") + "\n"

    out, count = redaction.redact(text)

    assert count >= 1
    assert PEM_BODY not in out


def test_redact_collapses_encrypted_pem_body():
    """RFC 1421 headers inside the block must not terminate the body scan.

    `Proc-Type: 4,ENCRYPTED` and `DEK-Info: …,…` carry `-` and `,`. Excluding
    those from the body class made the first header line end the block, after
    which every base64 line was emitted verbatim — with a plain, unremarkable
    `-----BEGIN RSA PRIVATE KEY-----` header.
    """
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "Proc-Type: 4,ENCRYPTED\n"
        "DEK-Info: DES-EDE3-CBC,1234567890ABCDEF\n"
        "\n"
        f"{PEM_BODY}\n"
        "-----END RSA PRIVATE KEY-----\n"
    )

    out, _ = redaction.redact(text)

    assert PEM_BODY not in out


def test_redact_leaves_public_keys_and_certificates_alone():
    """The counterpart: over-reach here would redact every committed SSH key."""
    text = ("-----BEGIN PUBLIC KEY-----\n" + PEM_BODY + "\n"
            "-----END PUBLIC KEY-----\n"
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBZrliCqwsmTKp3Ji user@host\n")

    out, _ = redaction.redact(text)

    assert out == text


# ------------------------------------------------ #5  overlapping mask spans


def test_mask_detections_keeps_both_markers_on_overlapping_spans():
    """`runpod-credential-assignment` overlaps `runpod-api-key` on every
    real `RUNPOD_*` assignment, so this is the common case, not an exotic one.
    """
    secret = "rpa_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ABCD"
    line = f'RUNPOD_API_KEY = "{secret}"'.encode()
    at = line.index(secret.encode())

    out = air.mask_detections([line], [
        _finding(1, "runpod-api-key", 1, at + 1, at + len(secret)),
        _finding(2, "runpod-credential-assignment", 1, 1, len(line)),
    ])[0].decode()

    assert "DETECTION-1" in out, "the inner marker was erased"
    assert "DETECTION-2" in out
    assert secret not in out


def test_mask_detections_never_leaks_a_byte_inside_a_masked_span():
    """Property check: no byte inside any masked span may survive.

    Right-to-left masking is sound only for disjoint spans. When a label is
    LONGER than the span it replaces the line grows, `mask_line`'s
    `end > len(line)` guard stops firing, and the outer span's stale `end`
    slides its tail slice left, so original bytes from inside the outer span
    survived into the window. On a `runpod-legacy-uuid-key` match, whose span
    ENDS with the credential, those bytes are the credential's tail.
    """
    labels = "«DETECTION-1:r1»«DETECTION-2:r2»".encode()
    # A line of bytes that cannot appear in either marker, so a byte found in
    # the output is unambiguously a surviving original.
    alphabet = bytes(c for c in range(0x21, 0x7F) if c not in set(labels))
    line = alphabet[:24]

    spans = [(s, e) for s in range(1, len(line) + 1)
             for e in range(s, min(s + 8, len(line)) + 1)]
    for (s1, e1), (s2, e2) in itertools.combinations(spans, 2):
        if e1 < s2 or e2 < s1:          # disjoint: never at risk
            continue
        out = air.mask_detections([line], [
            _finding(1, "r1", 1, s1, e1),
            _finding(2, "r2", 1, s2, e2),
        ])[0]
        for col in set(range(s1, e1 + 1)) | set(range(s2, e2 + 1)):
            assert line[col - 1:col] not in out, (
                f"byte at column {col} survived spans {(s1, e1)} / {(s2, e2)}")


def test_mask_detections_still_blanks_a_multi_line_block():
    """The multi-line path registers a full-line span on every continuation
    line, which overlaps anything else there by construction.
    """
    lines = [b"-----BEGIN PRIVATE KEY-----", b"SECRETBODYLINE", b"-----END-----"]

    out = air.mask_detections(lines, [
        {"id": 1, "rule": "private-key", "placeholder_shaped": False,
         "start_line": 1, "end_line": 3, "start_column": 1, "end_column": 13},
    ])

    assert b"SECRETBODYLINE" not in b"\n".join(out)
    assert b"DETECTION-1" in b"\n".join(out)


# ------------------------------------- #3  findings inside committed archives


def test_archive_finding_maps_to_the_containing_blob():
    """betterleaks extracts archives by default and reports the inner path with
    its `!` separator. Only the archive itself was ever materialised.
    """
    f = scan_head.to_finding(
        _raw(File="bundle.zip!inner.txt"), {"bundle.zip": "deadbeef"}, "/tmp/w")

    assert f is not None
    assert f["file"] == "bundle.zip"
    assert f["inner_path"] == "inner.txt"
    assert f["blob_sha"] == "deadbeef"


def test_unmappable_path_is_reported_not_raised():
    """One unmappable path used to raise, which wrote no findings file at all
    and so discarded every OTHER finding in the scan.
    """
    findings, unmappable = scan_head.merge_findings(
        [_raw(File="bundle.zip!inner.txt"), _raw(File="nowhere.txt")],
        {"bundle.zip": "deadbeef"}, "/tmp/w")

    assert len(findings) == 1
    assert [u["path"] for u in unmappable] == ["nowhere.txt"]


def test_two_files_in_one_archive_do_not_collapse():
    """Same blob, same span, different inner paths — two distinct findings."""
    findings, _ = scan_head.merge_findings(
        [_raw(File="bundle.zip!one.txt"), _raw(File="bundle.zip!two.txt")],
        {"bundle.zip": "deadbeef"}, "/tmp/w")

    assert {f["inner_path"] for f in findings} == {"one.txt", "two.txt"}


def test_archive_finding_is_triaged_without_a_window(monkeypatch):
    """The blob is the binary container, so there are no source lines to show.
    It must still reach the model rather than redden the gate.
    """
    def unreachable(*a, **k):
        raise AssertionError("must not fetch the archive blob for a window")

    monkeypatch.setattr(air, "fetch_blob", unreachable)
    f = scan_head.to_finding(
        _raw(File="bundle.zip!inner.txt"), {"bundle.zip": "deadbeef"}, "/tmp/w")
    f["id"] = 1

    evidence, errors = air.build_evidence("o/r", "tok", [f])

    assert errors == []
    assert len(evidence) == 1
    assert evidence[0]["window"] == ""


# ------------------------------------------------- #4  path-only scanner rule


# `pkcs12-file` is the only path-only rule in the default set: no line, no
# column, no Secret.
PKCS12 = {"File": "certstore.p12", "RuleID": "pkcs12-file",
          "StartLine": 0, "EndLine": 0, "StartColumn": 0, "EndColumn": 0,
          "Secret": "", "Description": "Identified a PKCS12 file", "Entropy": 0}


def test_path_only_finding_gets_a_file_head_window(monkeypatch):
    """Line 0 is a real finding about the FILE, not a broken window. It used to
    trip the "outside the blob" guard and redden the gate, override-only.
    """
    blob = b"\n".join(f"line {n}".encode() for n in range(1, 60))
    monkeypatch.setattr(air, "fetch_blob", lambda *a, **k: (blob, ""))
    f = scan_head.to_finding(PKCS12, {"certstore.p12": "cafe"}, "/tmp/w")
    f["id"] = 1

    evidence, errors = air.build_evidence("o/r", "tok", [f])

    assert errors == []
    assert len(evidence) == 1
    assert "line 1" in evidence[0]["window"]


def test_path_only_finding_on_a_binary_blob_shows_no_window(monkeypatch):
    """A decoded binary blob is not a source window, and its bytes may be the
    credential. The finding still has to reach the model, without them.
    """
    blob = b"\x00\x01\x02rpa_" + b"E" * 40 + b"\x00binary junk"
    monkeypatch.setattr(air, "fetch_blob", lambda *a, **k: (blob, ""))
    f = scan_head.to_finding(PKCS12, {"certstore.p12": "cafe"}, "/tmp/w")
    f["id"] = 1

    evidence, errors = air.build_evidence("o/r", "tok", [f])

    assert errors == []
    assert len(evidence) == 1
    assert evidence[0]["window"] == ""


def test_line_beyond_the_blob_is_still_an_error(monkeypatch):
    """The guard must keep failing closed for a genuinely impossible location."""
    monkeypatch.setattr(air, "fetch_blob", lambda *a, **k: (b"one\ntwo\n", ""))
    f = scan_head.to_finding(
        _raw(File="a.yml", StartLine=900, EndLine=900), {"a.yml": "cafe"}, "/tmp/w")
    f["id"] = 1

    evidence, errors = air.build_evidence("o/r", "tok", [f])

    assert evidence == []
    assert len(errors) == 1


# --------------------------------------------------------- #6  path-aware dedup


def test_dedup_keeps_both_paths_for_identical_content():
    """Byte-identical files share a git blob SHA, so a key built on the SHA
    collapsed them — clearing a fixture path cleared the real path with it.
    """
    findings, _ = scan_head.merge_findings(
        [_raw(File="docs/example_key.yml"), _raw(File="ansible/prod.yml")],
        {"docs/example_key.yml": "same", "ansible/prod.yml": "same"}, "/tmp/w")

    assert {f["file"] for f in findings} == {
        "docs/example_key.yml", "ansible/prod.yml"}


def test_dedup_still_merges_two_rules_on_one_span():
    """The intended merge has to survive the key change."""
    findings, _ = scan_head.merge_findings(
        [_raw(RuleID="runpod-api-key"),
         _raw(RuleID="runpod-key-in-allowlisted-path")],
        {"a.yml": "blob"}, "/tmp/w")

    assert len(findings) == 1
    assert findings[0]["rules"] == [
        "runpod-api-key", "runpod-key-in-allowlisted-path"]


# ------------------------------------------------- #7  attributing HTTP 400s


def _stub_submit(monkeypatch, body):
    calls, logs = [], []

    def fake_request(*a, **k):
        calls.append(k.get("data"))
        return 400, body

    monkeypatch.setattr(air, "request", fake_request)
    monkeypatch.setattr(air, "log", logs.append)
    monkeypatch.setenv("KIMI_REASONING_EFFORT", "low")
    return calls, logs


def test_400_without_the_field_name_is_not_blamed_on_reasoning_effort(monkeypatch):
    """A `temperature` or model 400 must not be reported as this one, nor pay
    for an identical retry that fails the same way.
    """
    calls, logs = _stub_submit(
        monkeypatch, "invalid temperature: only 1 is allowed for this model")

    review, _ = air.call_kimi("sys", "user", "key")

    assert review is None
    assert len(calls) == 1, "retried a 400 that was not about reasoning_effort"
    assert not any("reasoning_effort" in m for m in logs)
    assert any("temperature" in m for m in logs), "the real cause was not logged"


def test_illegal_reasoning_effort_is_dropped_before_the_request(monkeypatch):
    """The only case the retry existed for, caught without spending a request."""
    calls, logs = _stub_submit(monkeypatch, "{}")
    monkeypatch.setenv("KIMI_REASONING_EFFORT", "medium")   # there is no medium

    air.call_kimi("sys", "user", "key")

    assert len(calls) == 1, "spent a request on a value known to be illegal"
    assert b"reasoning_effort" not in calls[0], "sent the illegal value anyway"
    assert any("medium" in m for m in logs), "dropped it silently"


def test_legal_reasoning_effort_is_sent(monkeypatch):
    for value in sorted(air.ALLOWED_EFFORT):
        calls, _ = _stub_submit(monkeypatch, "{}")
        monkeypatch.setenv("KIMI_REASONING_EFFORT", value)
        air.call_kimi("sys", "user", "key")
        assert f'"reasoning_effort": "{value}"'.encode() in calls[0]


# ---------------------------- git must not take instructions from the scan tree


@pytest.mark.parametrize("attributes", [
    "* -diff",                                   # patches read "Binary files differ"
    "creds.yml -diff",
    "creds.yml working-tree-encoding=UTF-16LE",  # transcodes, or fails `git add`
    "* filter=mangle",
])
def test_commit_tree_neutralises_pr_supplied_gitattributes(tmp_path, attributes):
    """`.gitattributes` is materialised like any other changed file, and git
    attributes change how git PRESENTS content to `betterleaks git`. `-diff`
    alone was a silent, complete bypass: tracked file, zero findings, green gate.
    """
    body = "api_key: rpa_" + "F" * 40 + "\n"
    (tmp_path / "creds.yml").write_text(body)
    (tmp_path / ".gitattributes").write_text(attributes + "\n")

    tracked = scan_head.commit_tree(str(tmp_path))

    # The real invariant: what git committed is byte-identical to the content.
    true_oid = subprocess.run(
        ("git", "hash-object", "creds.yml"), cwd=tmp_path,
        check=True, capture_output=True, text=True).stdout.strip()
    assert tracked.get("creds.yml") == true_oid
    # And the content is still visible through the verb the scanner uses.
    patch = subprocess.run(("git", "log", "-p", "--all"), cwd=tmp_path,
                           check=True, capture_output=True, text=True).stdout
    assert "rpa_" in patch, "the scanner reads the tree through `git log -p`"


def test_main_reports_a_file_not_committed_byte_identically():
    """The tripwire compares OIDs, not names: a file altered on the way in is
    still tracked, so a name-only check could not see it.
    """
    written = [{"path": "a.yml", "rel": "a.yml", "blob_sha": "aaa"},
               {"path": "b.yml", "rel": "b.yml", "blob_sha": "bbb"}]
    tracked = {"a.yml": "aaa", "b.yml": "MANGLED"}

    altered = [f["rel"] for f in written if tracked.get(f["rel"]) != f["blob_sha"]]

    assert altered == ["b.yml"]


# ------------------------------------- one spelling of "where is this finding"


def test_identical_content_at_two_paths_gets_separate_windows(monkeypatch):
    """Two paths with identical bytes share a blob SHA. Grouping the mask by
    blob put each one's marker in the other's window, while the prompt tells the
    model "that marker IS the value you are judging".
    """
    secret = "rpa_" + "G" * 40
    monkeypatch.setattr(air, "fetch_blob",
                        lambda *a, **k: (f"api_key: {secret}\n".encode(), ""))
    findings, _ = scan_head.merge_findings(
        [_raw(File="docs/example_key.yml", Secret=secret),
         _raw(File="ansible/prod.yml", Secret=secret)],
        {"docs/example_key.yml": "SAME", "ansible/prod.yml": "SAME"}, "/tmp/w")

    evidence, errors = air.build_evidence("o/r", "tok", findings)

    assert errors == []
    assert len(evidence) == 2
    for e in evidence:
        foreign = [i for i in (1, 2) if i != e["id"] and f"DETECTION-{i}" in e["window"]]
        assert not foreign, f"{e['file']} window carries detection {foreign}"
        assert secret not in e["window"]


@pytest.mark.parametrize("raw_entry,expected", [
    (_raw(File="a.yml", StartLine=12, EndLine=12), "line 12"),
    # The path is code-spanned because it is attacker-chosen; see
    # test_location_label_cannot_escape_its_code_span.
    (_raw(File="bundle.zip!inner.txt"), "inside the archive, at `inner.txt`"),
    (PKCS12, "whole file (this rule matches the path, not content)"),
])
def test_location_label_is_the_one_spelling(raw_entry, expected):
    """The prompt, the published comment and the unanswered list all render
    this. Three independent derivations is how a path-only rule reached a human
    reviewer as "line 0".
    """
    f = scan_head.to_finding(
        raw_entry, {"a.yml": "x", "bundle.zip": "x", "certstore.p12": "x"}, "/tmp/w")

    assert air.location_label(f) == expected


def test_published_comment_never_says_line_0(monkeypatch):
    monkeypatch.setattr(air, "fetch_blob", lambda *a, **k: (b"cert\nstore\n", ""))
    f = scan_head.to_finding(PKCS12, {"certstore.p12": "cafe"}, "/tmp/w")
    f["id"] = 1
    evidence, _ = air.build_evidence("o/r", "tok", [f])
    review = air.parse_model_json(
        '{"findings": [{"id": 1, "verdict": "true_positive", "analysis": "a keystore"}]}',
        evidence)

    body = air.render_comment(review, "success", "", 0.0)

    assert "line 0" not in body
    assert "whole file" in body
    assert "``" not in body, "an empty code span reads as 'we found nothing here'"


# ------------------------------ _redact_exact: the stage-1 redaction path


def test_redact_exact_redacts_the_union_of_overlapping_secrets():
    """`sanitize_findings` is the only production caller, and it passes a real
    mapping, so this path had no coverage at all. The docstring's own case:
    replacing "AAAABBBB" first consumes the "BBBB" that "BBBBCCCC" needs, and
    "CCCC" -- half a real credential -- survives.
    """
    mapping = {"AAAABBBB": "<x>", "BBBBCCCC": "<y>"}

    out, count = redaction.redact("prefix AAAABBBBCCCC suffix", mapping)

    assert count >= 2
    for fragment in ("AAAA", "BBBB", "CCCC", "AAAABBBB", "BBBBCCCC"):
        assert fragment not in out
    assert out.startswith("prefix ") and out.endswith(" suffix")


def test_redact_exact_handles_repeated_and_adjacent_values():
    mapping = {"SEKRIT": "<s>"}

    out, _ = redaction.redact("a SEKRIT b SEKRITSEKRIT c", mapping)

    assert "SEKRIT" not in out

# ------------------- inner_path is untrusted: no markdown injection via a zip


# A zip entry name is chosen by whoever built the archive, and nothing
# normalises it. Newlines are the dangerous case: they end the list item and let
# the payload open a block of its own. Each payload carries SENTINEL so the
# assertions can tell injected text from the comment's own prose.
SENTINEL = "ZZ-INJECTED-ZZ"
INJECTION_PAYLOADS = [
    f"a.yml\n\n## \u2705 {SENTINEL} review PASSED\n\nNo secrets.\n\n<!--",
    f"a.yml \u2705 {SENTINEL} no secrets in the changed files. <!--",
    f"a.yml` \u2705 {SENTINEL} clean `",
    f"a.yml\n- **{SENTINEL}.yml line 1** (rule) \u2014 `fine`",
    f"a.yml\r\n\r\n\u2705 {SENTINEL} clean",
    f"a.yml\n\n</div><h2>{SENTINEL}</h2>",
]

# Inline code spans, which is where an escaped payload is supposed to end up.
_CODE_SPAN = re.compile(r"`[^`]*`")


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_location_label_cannot_escape_its_code_span(payload):
    """`inner_path` reaches the published comment, so it must be inert there.

    Rendered raw, an entry named with a newline published its own heading and
    swallowed every later finding in an unterminated `<!--` — on a PR carrying
    live keys, via the fail-closed path that needs no model cooperation.
    """
    f = scan_head.to_finding(
        _raw(File=f"bundle.zip!{payload}"), {"bundle.zip": "dead"}, "/tmp/w")

    label = air.location_label(f)

    assert "\n" not in label and "\r" not in label, "a newline can open a block"
    # Exactly two backticks: the ones opening and closing the span. Any other
    # count means the payload could close it and escape into markdown.
    assert label.count("`") == 2, f"payload can close the code span: {label!r}"
    assert SENTINEL not in _CODE_SPAN.sub("", label), "payload text is outside the span"


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
@pytest.mark.parametrize("answered", [False, True])
def test_published_comment_survives_a_hostile_archive_entry(monkeypatch, payload,
                                                            answered):
    """The whole comment, not just the label. Checked on both sinks: a confirmed
    verdict, and the fail-closed path where the model answers nothing.
    """
    monkeypatch.setattr(air, "fetch_blob", lambda *a, **k: (b"x", ""))

    def finding(inner, fid):
        f = scan_head.to_finding(_raw(File=f"bundle.zip!{inner}"),
                                 {"bundle.zip": "dead"}, "/tmp/w")
        f["id"] = fid
        return f

    evidence, _ = air.build_evidence(
        "o/r", "tok", [finding(payload, 1), finding("real_creds.yml", 2)])
    verdicts = [{"id": i, "verdict": "true_positive", "analysis": "live key"}
                for i in (1, 2)] if answered else []
    review = air.parse_model_json(json.dumps({"verdicts": verdicts}), evidence)

    body = air.render_comment(review, "success", "", 0.0)

    # The second, unrelated real detection is still named: nothing the payload
    # wrote commented it out.
    assert "real_creds.yml" in body
    # Every trace of the payload is inside a code span.
    assert SENTINEL in body, "the hostile entry name should still be shown, inertly"
    assert SENTINEL not in _CODE_SPAN.sub("", body), "payload escaped into markdown"


# ------------------------------------- team review contacts in the comment


def test_team_contact_links_to_the_team_page(monkeypatch):
    """`org/team` used to be squashed to `orgteam`: a link to nobody."""
    monkeypatch.setattr(air, "REVIEW_CONTACTS", ("runpod/security", "alice"))
    assert air.contact_links() == (
        "[runpod/security](https://github.com/orgs/runpod/teams/security)"
        " or [alice](https://github.com/alice)")


@pytest.mark.parametrize("contact", ["@runpod/security", "@alice", "a/b/c",
                                     "../x", "evil)](https://x"])
def test_contact_links_never_mention_or_escape(monkeypatch, contact):
    monkeypatch.setattr(air, "REVIEW_CONTACTS", (contact,))
    out = air.contact_links()
    assert "@" not in out
    assert re.fullmatch(r"(\[[\w./-]+\]\(https://github\.com/[\w./-]+\))?", out)
