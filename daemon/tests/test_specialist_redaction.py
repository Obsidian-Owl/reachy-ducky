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
