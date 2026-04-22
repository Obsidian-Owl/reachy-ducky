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

from reachy_ducky_daemon.specialists.pr_reviewer import _GH_TIMEOUT_SECONDS, _run_gh


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
