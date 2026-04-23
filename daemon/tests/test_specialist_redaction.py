"""Tests for :func:`redact`.

Unit tier mocks ``subprocess.run`` at the boundary — no real ``gitleaks``
invocation. A gated integration test at the bottom exercises the real
binary. Mirrors the shape of ``test_specialist_pr_reviewer.py``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from reachy_ducky_daemon.specialists.redaction import (
    _GITLEAKS_TIMEOUT_SECONDS,
    RedactionError,
    redact,
)


def _ok(stdout: str) -> subprocess.CompletedProcess[str]:
    """Shorthand for a successful ``CompletedProcess`` with canned stdout."""
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_gitleaks_timeout_matches_gh_timeout_precedent() -> None:
    """_GITLEAKS_TIMEOUT_SECONDS aligns with pr_reviewer's 30s gh timeout.

    Keeps the "subprocess operation time budget" consistent across
    specialist helpers — any future bump is a deliberate edit in one
    place.
    """
    assert _GITLEAKS_TIMEOUT_SECONDS == 30.0


def test_redact_passes_clean_text_through_unchanged(tmp_path: Path) -> None:
    """Empty JSON findings → input text unchanged, empty flag list."""
    with patch("subprocess.run", return_value=_ok("[]")):
        out, flags = redact("just benign text\nno secrets here\n", cwd=tmp_path)

    assert out == "just benign text\nno secrets here\n"
    assert flags == []


def test_redact_invokes_gitleaks_stdin_with_expected_flags(tmp_path: Path) -> None:
    """Argv + kwargs contract: gitleaks stdin with our flag set, cwd threaded through."""
    with patch("subprocess.run", return_value=_ok("[]")) as m:
        redact("anything", cwd=tmp_path)

    argv = m.call_args.args[0]
    assert argv[:2] == ["gitleaks", "stdin"]
    assert "--no-banner" in argv
    assert "--exit-code" in argv
    assert "0" in argv  # exit-code value
    assert "--report-format" in argv
    assert "json" in argv
    assert "--report-path" in argv
    assert "-" in argv  # stdout sentinel
    assert "--redact" in argv

    call_kwargs = m.call_args.kwargs
    assert call_kwargs["check"] is False
    assert call_kwargs["text"] is True
    assert call_kwargs["timeout"] == _GITLEAKS_TIMEOUT_SECONDS
    # Input is piped via stdin.
    assert call_kwargs["input"] == "anything"
    # cwd is threaded through so gitleaks runs in the project dir and
    # picks up any repo-local .gitleaks.toml (mirrors lefthook).
    assert call_kwargs["cwd"] == tmp_path


def test_redact_raises_on_missing_binary(tmp_path: Path) -> None:
    """FileNotFoundError from subprocess (binary missing) → RedactionError."""
    with patch("subprocess.run", side_effect=FileNotFoundError("gitleaks")):
        with pytest.raises(RedactionError, match="gitleaks"):
            redact("anything", cwd=tmp_path)


def test_redact_raises_on_timeout(tmp_path: Path) -> None:
    """TimeoutExpired → RedactionError with 'timeout' in the message."""
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="gitleaks", timeout=30.0),
    ):
        with pytest.raises(RedactionError, match="timeout"):
            redact("anything", cwd=tmp_path)


def test_redact_raises_on_non_zero_exit(tmp_path: Path) -> None:
    """Non-zero exit (gitleaks crashed) → RedactionError with stderr in message."""
    bad = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="gitleaks: fatal: bad config"
    )
    with patch("subprocess.run", return_value=bad):
        with pytest.raises(RedactionError, match="fatal: bad config"):
            redact("anything", cwd=tmp_path)


def test_redact_raises_on_malformed_json(tmp_path: Path) -> None:
    """Malformed JSON on stdout → RedactionError."""
    with patch("subprocess.run", return_value=_ok("not json")):
        with pytest.raises(RedactionError, match="JSON"):
            redact("anything", cwd=tmp_path)


def test_redact_raises_on_non_list_json(tmp_path: Path) -> None:
    """Unexpected JSON shape (object instead of list) → RedactionError."""
    with patch("subprocess.run", return_value=_ok('{"oops": true}')):
        with pytest.raises(RedactionError, match="list"):
            redact("anything", cwd=tmp_path)


def test_redact_raises_on_generic_subprocess_error(tmp_path: Path) -> None:
    """OSError / generic SubprocessError → RedactionError (not a crash)."""
    with patch("subprocess.run", side_effect=OSError("permission denied")):
        with pytest.raises(RedactionError, match="subprocess failed"):
            redact("anything", cwd=tmp_path)


def test_redact_raises_on_non_dict_finding_entry(tmp_path: Path) -> None:
    """JSON list entry that isn't an object → RedactionError (fail closed).

    Silently dropping malformed findings could leave secrets in the
    output, so any list element we can't narrow becomes an error.
    """
    with patch("subprocess.run", return_value=_ok('["not a dict"]')):
        with pytest.raises(RedactionError, match="not an object"):
            redact("anything", cwd=tmp_path)


def test_redact_raises_on_finding_missing_required_field(tmp_path: Path) -> None:
    """Finding object missing RuleID/StartLine/etc. → RedactionError."""
    incomplete = '[{"RuleID": "github-pat", "StartLine": 1}]'
    with patch("subprocess.run", return_value=_ok(incomplete)):
        with pytest.raises(RedactionError, match="missing required field"):
            redact("anything", cwd=tmp_path)


def test_redact_raises_on_finding_out_of_range(tmp_path: Path) -> None:
    """Finding coords beyond the parsed line count → RedactionError (fail closed).

    gitleaks and our ``text.split('\\n')`` disagreeing about line structure
    means we can't trust the splice coordinates — better to abort than
    risk a mis-placed marker that leaves a secret partially in.
    """
    # One-line input, finding claims it's on line 99.
    findings_out_of_range = (
        '[{"RuleID": "x", "StartLine": 99, "EndLine": 99, "StartColumn": 1, "EndColumn": 5}]'
    )
    with patch("subprocess.run", return_value=_ok(findings_out_of_range)):
        with pytest.raises(RedactionError, match="out of range"):
            redact("short input", cwd=tmp_path)


def test_redact_raises_on_non_positive_start_column(tmp_path: Path) -> None:
    """StartColumn ≤ 0 → RedactionError (Python slicing with negatives silently wraps)."""
    bad = '[{"RuleID": "x", "StartLine": 1, "EndLine": 1, "StartColumn": 0, "EndColumn": 5}]'
    with patch("subprocess.run", return_value=_ok(bad)):
        with pytest.raises(RedactionError, match="invalid column range"):
            redact("short input", cwd=tmp_path)


def test_redact_raises_on_end_before_start(tmp_path: Path) -> None:
    """EndColumn < StartColumn → RedactionError (would splice the wrong range)."""
    bad = '[{"RuleID": "x", "StartLine": 1, "EndLine": 1, "StartColumn": 8, "EndColumn": 3}]'
    with patch("subprocess.run", return_value=_ok(bad)):
        with pytest.raises(RedactionError, match="invalid column range"):
            redact("short input", cwd=tmp_path)


def test_redact_raises_on_end_column_past_line_length(tmp_path: Path) -> None:
    """EndColumn past line length → RedactionError.

    Silent Python slice clamping would leave a partial secret behind;
    fail closed instead.
    """
    # "short" is 5 chars; EndColumn 999 would clamp silently if we didn't validate.
    bad = '[{"RuleID": "x", "StartLine": 1, "EndLine": 1, "StartColumn": 1, "EndColumn": 999}]'
    with patch("subprocess.run", return_value=_ok(bad)):
        with pytest.raises(RedactionError, match="exceeds line"):
            redact("short", cwd=tmp_path)


def test_redact_raises_on_multiline_start_column_past_line_length(tmp_path: Path) -> None:
    """Multi-line finding with StartColumn past start-line length → RedactionError."""
    bad = '[{"RuleID": "x", "StartLine": 1, "EndLine": 2, "StartColumn": 999, "EndColumn": 1}]'
    with patch("subprocess.run", return_value=_ok(bad)):
        with pytest.raises(RedactionError, match="StartColumn"):
            redact("short\nlines here\n", cwd=tmp_path)


def test_redact_raises_on_inverted_line_range(tmp_path: Path) -> None:
    """StartLine > EndLine → RedactionError (Codex P2, PR #52 second review).

    Python's ``del lines[start+1:end+1]`` silently no-ops on a reversed
    slice, which would leave the original secret text in place while the
    marker landed on the wrong line. Fail closed instead.
    """
    bad = '[{"RuleID": "x", "StartLine": 3, "EndLine": 1, "StartColumn": 1, "EndColumn": 3}]'
    with patch("subprocess.run", return_value=_ok(bad)):
        with pytest.raises(RedactionError, match="inverted line range"):
            redact("aaa\nbbb\nccc\nddd\n", cwd=tmp_path)


# ---------------------------------------------------------------------------
# Splicing logic — single-line, multi-line, dedup
# ---------------------------------------------------------------------------


def _finding_json(findings: list[dict[str, object]]) -> str:
    """Render a list of gitleaks-shaped finding dicts as JSON stdout."""
    import json as _json

    return _json.dumps(findings)


def test_redact_splices_single_finding(tmp_path: Path) -> None:
    """One github-pat → marker replaces the match, rule id returned.

    Concatenation form keeps the source file out of lefthook's
    gitleaks-staged rule while the runtime value is a 40-char
    shape-matching fake. The subprocess mock returns canned findings
    regardless of what we pass in.
    """
    input_text = "github_pat = ghp_" + ("x" * 36) + "\n"  # gitleaks:allow
    findings = [
        {
            "RuleID": "github-pat",
            "StartLine": 1,
            "EndLine": 1,
            "StartColumn": 14,  # 1-indexed start of the ghp_ token
            "EndColumn": 53,  # end-inclusive, 1-indexed, 40-char span
        }
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        out, flags = redact(input_text, cwd=tmp_path)

    # The shape-matching "token" built by concatenation above must be gone.
    assert ("ghp_" + ("x" * 36)) not in out  # gitleaks:allow
    assert "[REDACTED:github-pat]" in out
    assert flags == ["github-pat"]


def test_redact_deduplicates_same_rule(tmp_path: Path) -> None:
    """Three github-pat findings on separate lines → three splices, one flag entry."""
    input_text = (
        "a = ghp_" + ("a" * 36) + "\n"  # gitleaks:allow
        "b = ghp_" + ("b" * 36) + "\n"  # gitleaks:allow
        "c = ghp_" + ("c" * 36) + "\n"  # gitleaks:allow
    )
    findings = [
        {
            "RuleID": "github-pat",
            "StartLine": i,
            "EndLine": i,
            "StartColumn": 5,
            "EndColumn": 44,
        }
        for i in (1, 2, 3)
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        out, flags = redact(input_text, cwd=tmp_path)

    assert out.count("[REDACTED:github-pat]") == 3
    assert flags == ["github-pat"]


def test_redact_preserves_rule_order_across_types(tmp_path: Path) -> None:
    """Multiple distinct rules → flag list preserves first-occurrence order."""
    input_text = "line1 with github\nline2 with aws\n"
    findings = [
        {
            "RuleID": "github-pat",
            "StartLine": 1,
            "EndLine": 1,
            "StartColumn": 12,
            "EndColumn": 17,
        },
        {
            "RuleID": "aws-access-key",
            "StartLine": 2,
            "EndLine": 2,
            "StartColumn": 12,
            "EndColumn": 14,
        },
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        _, flags = redact(input_text, cwd=tmp_path)

    assert flags == ["github-pat", "aws-access-key"]


def test_redact_collapses_multi_line_finding_to_single_marker(tmp_path: Path) -> None:
    """Multi-line finding → one marker replaces the entire range.

    Uses generic placeholder lines rather than a real-looking private
    key block — that literal shape would trip gitleaks' ``private-key``
    rule at commit time on this source file. The mocked subprocess
    returns canned findings regardless.
    """
    input_text = (
        "context before\n"
        "PLACEHOLDER_LINE_1\n"
        "PLACEHOLDER_LINE_2_WITH_KEY_MATERIAL\n"
        "PLACEHOLDER_LINE_3\n"
        "context after\n"
    )
    findings = [
        {
            "RuleID": "private-key",
            "StartLine": 2,
            "EndLine": 4,
            "StartColumn": 1,
            "EndColumn": 18,  # length of "PLACEHOLDER_LINE_3"
        }
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        out, flags = redact(input_text, cwd=tmp_path)

    assert "PLACEHOLDER_LINE_1" not in out
    assert "PLACEHOLDER_LINE_2_WITH_KEY_MATERIAL" not in out
    assert "PLACEHOLDER_LINE_3" not in out
    assert "[REDACTED:private-key]" in out
    # Bookend context preserved.
    assert "context before" in out
    assert "context after" in out
    assert flags == ["private-key"]


def test_redact_splices_without_shifting_earlier_findings(tmp_path: Path) -> None:
    """Two findings on the same line: marker for the later one doesn't shift the earlier."""
    input_text = "alpha AAAAAAAA beta BBBBBBBB end\n"
    findings = [
        {"RuleID": "rule-a", "StartLine": 1, "EndLine": 1, "StartColumn": 7, "EndColumn": 14},
        {"RuleID": "rule-b", "StartLine": 1, "EndLine": 1, "StartColumn": 21, "EndColumn": 28},
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        out, _ = redact(input_text, cwd=tmp_path)

    assert "AAAAAAAA" not in out
    assert "BBBBBBBB" not in out
    assert out.count("[REDACTED:rule-a]") == 1
    assert out.count("[REDACTED:rule-b]") == 1
    # Bookend words still present and in order.
    assert out.index("alpha") < out.index("[REDACTED:rule-a]") < out.index("beta")
    assert out.index("beta") < out.index("[REDACTED:rule-b]") < out.index("end")


def test_redact_multiline_allows_end_column_less_than_start_column(tmp_path: Path) -> None:
    """Multi-line finding where EndColumn < StartColumn is legitimate.

    StartColumn and EndColumn apply to different lines when StartLine <
    EndLine, so there's no cross-line ordering relationship between
    them — ``col 7 on line 1`` to ``col 4 on line 2`` is a valid span.
    Regression guard: an earlier check rejected this case unconditionally
    and had to be narrowed to same-line findings.
    """
    # Span: from col 7 on line 1 through col 4 on line 2 (inclusive).
    findings = [
        {
            "RuleID": "private-key",
            "StartLine": 1,
            "EndLine": 2,
            "StartColumn": 7,
            "EndColumn": 4,
        }
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        out, flags = redact("prefix-SECRET\nKEY-suffix\n", cwd=tmp_path)

    assert "[REDACTED:private-key]" in out
    assert flags == ["private-key"]


def test_redact_merges_overlapping_same_line_findings(tmp_path: Path) -> None:
    """Overlapping findings on one line → single composite marker with both rule IDs.

    Codex P1 (PR #52 second review): descending sort only preserves
    coordinate stability for disjoint intervals. If gitleaks emits two
    findings that share a position (same token matching two rules),
    splicing one mutates the coordinate space the other was measured in
    and can leave a secret fragment behind.
    """
    # Same token at cols 1..14 matches two rules. A (broad) covers 1..14,
    # B (specific) covers 5..14. Overlap → merged span 1..14 with both
    # rule IDs, first-occurrence order.
    findings = [
        {
            "RuleID": "generic-api-key",
            "StartLine": 1,
            "EndLine": 1,
            "StartColumn": 1,
            "EndColumn": 14,
        },
        {
            "RuleID": "github-pat",
            "StartLine": 1,
            "EndLine": 1,
            "StartColumn": 5,
            "EndColumn": 14,
        },
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        out, flags = redact("ghp_ABCDEFGHIJ rest", cwd=tmp_path)

    # Exactly one splice (not two) — the overlap merged into one composite.
    assert out.count("[REDACTED:") == 1
    # Marker carries both rule IDs, comma-joined, first-occurrence order.
    assert "[REDACTED:generic-api-key,github-pat]" in out
    # Original token gone.
    assert "ghp_ABCDEFGHIJ" not in out
    assert "rest" in out
    # Flags list both rules, deduped, first-occurrence order.
    assert flags == ["generic-api-key", "github-pat"]


def test_redact_merges_overlapping_multi_line_findings(tmp_path: Path) -> None:
    """Overlap across lines → single composite marker spanning the union."""
    # A: line 1 col 1 → line 2 col 3.
    # B: line 2 col 2 → line 3 col 5. They share position (2, 2..3).
    # Merged: line 1 col 1 → line 3 col 5, rule_ids=[A, B].
    findings = [
        {
            "RuleID": "rule-a",
            "StartLine": 1,
            "EndLine": 2,
            "StartColumn": 1,
            "EndColumn": 3,
        },
        {
            "RuleID": "rule-b",
            "StartLine": 2,
            "EndLine": 3,
            "StartColumn": 2,
            "EndColumn": 5,
        },
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        out, flags = redact("aaaaa\nbbbbb\nccccc\n", cwd=tmp_path)

    assert out.count("[REDACTED:") == 1
    assert "[REDACTED:rule-a,rule-b]" in out
    assert flags == ["rule-a", "rule-b"]


def test_redact_non_overlapping_findings_stay_separate(tmp_path: Path) -> None:
    """Non-overlapping findings must NOT merge — two markers, disjoint splices.

    Regression guard for the overlap-merge pass: ranges [1..5] and
    [7..11] share no position (col 6 sits between them), so the merge
    logic must keep them separate. Otherwise adjacent findings would
    collapse unnecessarily and lose per-rule attribution.
    """
    findings = [
        {
            "RuleID": "rule-a",
            "StartLine": 1,
            "EndLine": 1,
            "StartColumn": 1,
            "EndColumn": 5,
        },
        {
            "RuleID": "rule-b",
            "StartLine": 1,
            "EndLine": 1,
            "StartColumn": 7,
            "EndColumn": 11,
        },
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        out, flags = redact("AAAAA BBBBB end", cwd=tmp_path)

    assert out.count("[REDACTED:rule-a]") == 1
    assert out.count("[REDACTED:rule-b]") == 1
    # No composite marker.
    assert "[REDACTED:rule-a,rule-b]" not in out
    assert flags == ["rule-a", "rule-b"]


# ---------------------------------------------------------------------------
# Integration (gated) — real gitleaks binary
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_redact_real_gitleaks_catches_synthetic_pat(tmp_path: Path) -> None:
    """End-to-end smoke: pipe a synthetic GitHub PAT through real gitleaks.

    Gated on ``REACHY_DUCKY_RUN_INTEGRATION=1`` — the only test in
    this module that requires the actual binary on PATH. Mirrors the
    gating pattern from
    ``test_specialist_pr_reviewer.test_pr_reviewer_live_claude``.

    Uses a concatenated PAT shape so the source file itself stays out
    of gitleaks' commit-time scan; the runtime value is 40 chars of
    ``ghp_`` + 36 mixed-case alphanumeric. The 36-char body must have
    enough entropy (~5.0) to clear gitleaks' ``github-pat`` rule —
    homogeneous strings like ``"A" * 36`` won't trigger it.
    """
    if not os.environ.get("REACHY_DUCKY_RUN_INTEGRATION"):
        pytest.skip("set REACHY_DUCKY_RUN_INTEGRATION=1 to run")

    # High-entropy mixed-case body (36 chars). Built via concatenation so
    # the literal in source doesn't match gitleaks' regex.
    fake_pat = "ghp_" + "xK9Pm2Qw7nL8tRa5VcFgHjKbNdEfShUzMvXy"  # gitleaks:allow
    synthetic = f"benign preamble\ngithub_pat = {fake_pat}\npostamble line\n"

    redacted, flags = redact(synthetic, cwd=tmp_path)

    assert fake_pat not in redacted
    assert "[REDACTED:github-pat]" in redacted
    assert "github-pat" in flags
    # Non-secret content preserved.
    assert "benign preamble" in redacted
    assert "postamble line" in redacted
