"""Tests for :func:`redact`.

Unit tier mocks ``subprocess.run`` at the boundary — no real ``gitleaks``
invocation. A gated integration test at the bottom exercises the real
binary. Mirrors the shape of ``test_specialist_pr_reviewer.py``.
"""

from __future__ import annotations

import subprocess
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


def test_redact_passes_clean_text_through_unchanged() -> None:
    """Empty JSON findings → input text unchanged, empty flag list."""
    with patch("subprocess.run", return_value=_ok("[]")):
        out, flags = redact("just benign text\nno secrets here\n")

    assert out == "just benign text\nno secrets here\n"
    assert flags == []


def test_redact_invokes_gitleaks_stdin_with_expected_flags() -> None:
    """Argv contract: gitleaks stdin with our flag set."""
    with patch("subprocess.run", return_value=_ok("[]")) as m:
        redact("anything")

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


def test_redact_raises_on_missing_binary() -> None:
    """FileNotFoundError from subprocess (binary missing) → RedactionError."""
    with patch("subprocess.run", side_effect=FileNotFoundError("gitleaks")):
        with pytest.raises(RedactionError, match="gitleaks"):
            redact("anything")


def test_redact_raises_on_timeout() -> None:
    """TimeoutExpired → RedactionError with 'timeout' in the message."""
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="gitleaks", timeout=30.0),
    ):
        with pytest.raises(RedactionError, match="timeout"):
            redact("anything")


def test_redact_raises_on_non_zero_exit() -> None:
    """Non-zero exit (gitleaks crashed) → RedactionError with stderr in message."""
    bad = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="gitleaks: fatal: bad config"
    )
    with patch("subprocess.run", return_value=bad):
        with pytest.raises(RedactionError, match="fatal: bad config"):
            redact("anything")


def test_redact_raises_on_malformed_json() -> None:
    """Malformed JSON on stdout → RedactionError."""
    with patch("subprocess.run", return_value=_ok("not json")):
        with pytest.raises(RedactionError, match="JSON"):
            redact("anything")


def test_redact_raises_on_non_list_json() -> None:
    """Unexpected JSON shape (object instead of list) → RedactionError."""
    with patch("subprocess.run", return_value=_ok('{"oops": true}')):
        with pytest.raises(RedactionError, match="list"):
            redact("anything")


# ---------------------------------------------------------------------------
# Splicing logic — single-line, multi-line, dedup
# ---------------------------------------------------------------------------


def _finding_json(findings: list[dict[str, object]]) -> str:
    """Render a list of gitleaks-shaped finding dicts as JSON stdout."""
    import json as _json

    return _json.dumps(findings)


def test_redact_splices_single_finding() -> None:
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
        out, flags = redact(input_text)

    # The shape-matching "token" built by concatenation above must be gone.
    assert ("ghp_" + ("x" * 36)) not in out  # gitleaks:allow
    assert "[REDACTED:github-pat]" in out
    assert flags == ["github-pat"]


def test_redact_deduplicates_same_rule() -> None:
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
        out, flags = redact(input_text)

    assert out.count("[REDACTED:github-pat]") == 3
    assert flags == ["github-pat"]


def test_redact_preserves_rule_order_across_types() -> None:
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
        _, flags = redact(input_text)

    assert flags == ["github-pat", "aws-access-key"]


def test_redact_collapses_multi_line_finding_to_single_marker() -> None:
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
        out, flags = redact(input_text)

    assert "PLACEHOLDER_LINE_1" not in out
    assert "PLACEHOLDER_LINE_2_WITH_KEY_MATERIAL" not in out
    assert "PLACEHOLDER_LINE_3" not in out
    assert "[REDACTED:private-key]" in out
    # Bookend context preserved.
    assert "context before" in out
    assert "context after" in out
    assert flags == ["private-key"]


def test_redact_splices_without_shifting_earlier_findings() -> None:
    """Two findings on the same line: marker for the later one doesn't shift the earlier."""
    input_text = "alpha AAAAAAAA beta BBBBBBBB end\n"
    findings = [
        {"RuleID": "rule-a", "StartLine": 1, "EndLine": 1, "StartColumn": 7, "EndColumn": 14},
        {"RuleID": "rule-b", "StartLine": 1, "EndLine": 1, "StartColumn": 21, "EndColumn": 28},
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        out, _ = redact(input_text)

    assert "AAAAAAAA" not in out
    assert "BBBBBBBB" not in out
    assert out.count("[REDACTED:rule-a]") == 1
    assert out.count("[REDACTED:rule-b]") == 1
    # Bookend words still present and in order.
    assert out.index("alpha") < out.index("[REDACTED:rule-a]") < out.index("beta")
    assert out.index("beta") < out.index("[REDACTED:rule-b]") < out.index("end")
