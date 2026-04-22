"""Tests for :class:`PRReviewer`.

Follows the same shape as ``test_specialist_plan_reviewer.py`` — subprocess
calls are mocked at the ``subprocess.run`` boundary (rather than running
real ``gh``, which would need network + auth + a live PR). Canned ``gh``
outputs live under ``daemon/tests/fixtures/gh_*.json``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from reachy_ducky_daemon.specialists.pr_reviewer import (
    _GH_TIMEOUT_SECONDS,
    _fetch_pr_metadata,
    _run_gh,
)


def test_run_gh_invokes_list_form_with_timeout() -> None:
    """_run_gh builds a list-form argv and passes the module-wide timeout.

    Never ``shell=True``; list form prevents injection via args. The
    timeout mirrors ``_GIT_TIMEOUT_SECONDS`` in plan_reviewer.py:68 so a
    hanging subprocess cannot wedge the review forever.
    """
    fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
    with patch("subprocess.run", return_value=fake_proc) as m:
        result = _run_gh(["pr", "view", "42"], cwd=Path("/tmp"))

    assert result.stdout == "ok"
    m.assert_called_once()
    assert m.call_args.args[0] == ["gh", "pr", "view", "42"]
    call_kwargs = m.call_args.kwargs
    assert call_kwargs["cwd"] == Path("/tmp")
    assert call_kwargs["check"] is False
    assert call_kwargs["capture_output"] is True
    assert call_kwargs["text"] is True
    assert call_kwargs["timeout"] == _GH_TIMEOUT_SECONDS


def test_gh_timeout_matches_git_timeout_precedent() -> None:
    """_GH_TIMEOUT_SECONDS mirrors plan_reviewer's 30s git timeout.

    Diverging would be surprising; anchor the value here so any future
    raise is a deliberate edit in one place.
    """
    assert _GH_TIMEOUT_SECONDS == 30.0


# ---------------------------------------------------------------------------
# PR metadata fetch
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"


def test_fetch_pr_metadata_parses_gh_pr_view_json(tmp_path: Path) -> None:
    """_fetch_pr_metadata invokes ``gh pr view <num> --json ...`` and parses output."""
    fixture = _FIXTURES / "gh_pr_view_happy.json"
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=fixture.read_text(), stderr=""
    )
    with patch("subprocess.run", return_value=fake_proc) as m:
        meta, err = _fetch_pr_metadata(pr_number=42, cwd=tmp_path)

    assert err is None
    assert meta["number"] == 42
    assert meta["title"] == "feat: add retry logic"
    assert meta["state"] == "OPEN"
    assert meta["headRefName"] == "feat-retry"
    assert meta["headRefOid"] == "abc123def456"
    # List form + --json field contract.
    argv = m.call_args.args[0]
    assert argv[:3] == ["gh", "pr", "view"]
    assert "42" in argv
    assert "--json" in argv


def test_fetch_pr_metadata_surfaces_gh_failure_as_diagnostic(tmp_path: Path) -> None:
    """Non-zero ``gh`` exit returns ``({}, error_string)`` — no exception."""
    fake_proc = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="GraphQL: Could not resolve to a PullRequest",
    )
    with patch("subprocess.run", return_value=fake_proc):
        meta, err = _fetch_pr_metadata(pr_number=999999, cwd=tmp_path)

    assert meta == {}
    assert err is not None
    assert "999999" in err or "Could not resolve" in err


def test_fetch_pr_metadata_surfaces_unparseable_json_as_diagnostic(tmp_path: Path) -> None:
    """Malformed stdout returns ``({}, error_string)`` rather than raising JSONDecodeError."""
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="not json at all", stderr=""
    )
    with patch("subprocess.run", return_value=fake_proc):
        meta, err = _fetch_pr_metadata(pr_number=42, cwd=tmp_path)

    assert meta == {}
    assert err is not None
    assert "unparseable" in err.lower() or "json" in err.lower()
