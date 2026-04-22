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
    _current_branch,
    _derive_flags,
    _fetch_check_runs,
    _fetch_diff,
    _fetch_pr_metadata,
    _fetch_review_comments,
    _find_pr_for_branch,
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


# ---------------------------------------------------------------------------
# Diff fetch
# ---------------------------------------------------------------------------


def test_fetch_diff_invokes_gh_pr_diff(tmp_path: Path) -> None:
    """_fetch_diff runs ``gh pr diff <num>`` and returns ``(stdout, None)`` on success."""
    fake_proc = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="diff --git a/src/x.py b/src/x.py\n+x = 2\n",
        stderr="",
    )
    with patch("subprocess.run", return_value=fake_proc) as m:
        diff, err = _fetch_diff(pr_number=42, cwd=tmp_path)

    assert err is None
    assert "+x = 2" in diff
    assert m.call_args.args[0] == ["gh", "pr", "diff", "42"]


def test_fetch_diff_surfaces_failure_as_diagnostic(tmp_path: Path) -> None:
    """Non-zero ``gh`` exit returns ``("", error_string)`` — no exception."""
    fake_proc = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no such PR")
    with patch("subprocess.run", return_value=fake_proc):
        diff, err = _fetch_diff(pr_number=42, cwd=tmp_path)

    assert diff == ""
    assert err is not None and "no such PR" in err


# ---------------------------------------------------------------------------
# Review comments fetch
# ---------------------------------------------------------------------------


def test_fetch_review_comments_calls_gh_api(tmp_path: Path) -> None:
    """_fetch_review_comments hits /repos/{owner}/{repo}/pulls/{num}/comments."""
    fixture = _FIXTURES / "gh_api_comments.json"
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=fixture.read_text(), stderr=""
    )
    with patch("subprocess.run", return_value=fake_proc) as m:
        comments, err = _fetch_review_comments(
            owner="Obsidian-Owl", repo="reachy-ducky", pr_number=42, cwd=tmp_path
        )

    assert err is None
    assert len(comments) == 2
    first_user = comments[0]["user"]
    assert isinstance(first_user, dict)
    assert first_user["login"] == "augment-code[bot]"
    argv = m.call_args.args[0]
    assert argv[:2] == ["gh", "api"]
    assert "/repos/Obsidian-Owl/reachy-ducky/pulls/42/comments" in argv
    assert "--paginate" in argv


def test_fetch_review_comments_empty_on_failure(tmp_path: Path) -> None:
    """404/auth/network failures return ``([], error)`` — no exception."""
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="404 Not Found"
    )
    with patch("subprocess.run", return_value=fake_proc):
        comments, err = _fetch_review_comments(owner="o", repo="r", pr_number=1, cwd=tmp_path)

    assert comments == []
    assert err is not None


def test_fetch_review_comments_surfaces_non_list_json_as_diagnostic(
    tmp_path: Path,
) -> None:
    """GitHub returning a non-list (e.g. error envelope) becomes a diagnostic."""
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout='{"message": "API rate limit"}', stderr=""
    )
    with patch("subprocess.run", return_value=fake_proc):
        comments, err = _fetch_review_comments(owner="o", repo="r", pr_number=1, cwd=tmp_path)

    assert comments == []
    assert err is not None
    assert "non-list" in err.lower() or "unexpected" in err.lower()


# ---------------------------------------------------------------------------
# CI check-runs fetch
# ---------------------------------------------------------------------------


def test_fetch_check_runs_calls_gh_api_at_head_sha(tmp_path: Path) -> None:
    """_fetch_check_runs hits /repos/{owner}/{repo}/commits/{sha}/check-runs."""
    fixture = _FIXTURES / "gh_api_check_runs.json"
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=fixture.read_text(), stderr=""
    )
    with patch("subprocess.run", return_value=fake_proc) as m:
        runs, err = _fetch_check_runs(
            owner="Obsidian-Owl", repo="reachy-ducky", head_sha="abc123", cwd=tmp_path
        )

    assert err is None
    assert len(runs) == 3
    assert runs[0]["name"] == "ruff"
    argv = m.call_args.args[0]
    assert argv[:2] == ["gh", "api"]
    assert "/repos/Obsidian-Owl/reachy-ducky/commits/abc123/check-runs" in argv


def test_fetch_check_runs_unwraps_github_envelope(tmp_path: Path) -> None:
    """GitHub wraps check-runs in ``{total_count, check_runs: [...]}`` — unwrap."""
    envelope = '{"total_count": 1, "check_runs": [{"name": "mypy", "conclusion": "success"}]}'
    fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=envelope, stderr="")
    with patch("subprocess.run", return_value=fake_proc):
        runs, err = _fetch_check_runs(owner="o", repo="r", head_sha="sha", cwd=tmp_path)

    assert err is None
    assert runs == [{"name": "mypy", "conclusion": "success"}]


def test_fetch_check_runs_surfaces_unexpected_shape_as_diagnostic(
    tmp_path: Path,
) -> None:
    """Missing ``check_runs`` key (unexpected shape) becomes a diagnostic."""
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout='{"total_count": 0}', stderr=""
    )
    with patch("subprocess.run", return_value=fake_proc):
        runs, err = _fetch_check_runs(owner="o", repo="r", head_sha="sha", cwd=tmp_path)

    assert runs == []
    assert err is not None
    assert "unexpected" in err.lower() or "shape" in err.lower()


# ---------------------------------------------------------------------------
# Auto-detect: current branch + find-PR-for-branch
# ---------------------------------------------------------------------------


def test_current_branch_reads_git_rev_parse(tmp_path: Path) -> None:
    """_current_branch shells ``git rev-parse --abbrev-ref HEAD``."""
    fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="feat-retry\n", stderr="")
    with patch("subprocess.run", return_value=fake_proc) as m:
        branch, err = _current_branch(tmp_path)

    assert branch == "feat-retry"
    assert err is None
    assert m.call_args.args[0] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]


def test_current_branch_surfaces_git_failure_as_diagnostic(tmp_path: Path) -> None:
    """git rev-parse failure returns ``("unknown", error)`` — no exception."""
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=128, stdout="", stderr="fatal: not a git repository"
    )
    with patch("subprocess.run", return_value=fake_proc):
        branch, err = _current_branch(tmp_path)

    assert branch == "unknown"
    assert err is not None
    assert "not a git repository" in err


def test_find_pr_for_branch_picks_first_open(tmp_path: Path) -> None:
    """_find_pr_for_branch returns the first open PR number for ``head=<branch>``."""
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout='[{"number": 42, "state": "OPEN"}]', stderr=""
    )
    with patch("subprocess.run", return_value=fake_proc) as m:
        pr_number, err = _find_pr_for_branch(branch="feat-retry", cwd=tmp_path)

    assert pr_number == 42
    assert err is None
    argv = m.call_args.args[0]
    assert argv[:3] == ["gh", "pr", "list"]
    assert "--head" in argv
    assert "feat-retry" in argv
    assert "--state" in argv
    assert "open" in argv


def test_find_pr_for_branch_none_when_no_pr(tmp_path: Path) -> None:
    """Empty ``gh`` list → ``(None, None)`` — absence of a PR is not an error."""
    fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
    with patch("subprocess.run", return_value=fake_proc):
        pr_number, err = _find_pr_for_branch(branch="feat-retry", cwd=tmp_path)

    assert pr_number is None
    assert err is None


def test_find_pr_for_branch_surfaces_gh_failure_as_diagnostic(tmp_path: Path) -> None:
    """Non-zero ``gh`` exit returns ``(None, error)``.

    Distinguishes "no PR for this branch" (None, None) from
    "couldn't ask GitHub" (None, error_string) — the orchestrator
    routes these to different prompts.
    """
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="authentication required"
    )
    with patch("subprocess.run", return_value=fake_proc):
        pr_number, err = _find_pr_for_branch(branch="feat-retry", cwd=tmp_path)

    assert pr_number is None
    assert err is not None
    assert "authentication" in err.lower() or "failed" in err.lower()


# ---------------------------------------------------------------------------
# Flag derivation
# ---------------------------------------------------------------------------


def test_derive_flags_no_pr_found_short_circuits() -> None:
    """Empty PR dict → ``no-pr-found`` and nothing else (other fields are meaningless)."""
    flags = _derive_flags(pr={}, comments=[], check_runs=[])
    assert flags == ["no-pr-found"]


def test_derive_flags_ci_green_when_all_success() -> None:
    """All check-runs conclude success → ``ci-green``."""
    runs: list[dict[str, object]] = [
        {"conclusion": "success"},
        {"conclusion": "success"},
    ]
    flags = _derive_flags(pr={"number": 1}, comments=[], check_runs=runs)
    assert "ci-green" in flags
    assert "ci-red" not in flags
    assert "ci-pending" not in flags


def test_derive_flags_ci_red_when_any_failure() -> None:
    """Any failure/cancelled/timed_out → ``ci-red`` (failure wins over pending)."""
    runs: list[dict[str, object]] = [
        {"conclusion": "success"},
        {"conclusion": "failure"},
        {"status": "in_progress", "conclusion": None},
    ]
    flags = _derive_flags(pr={"number": 1}, comments=[], check_runs=runs)
    assert "ci-red" in flags
    assert "ci-green" not in flags
    assert "ci-pending" not in flags


def test_derive_flags_ci_pending_when_in_progress_without_failure() -> None:
    """Only pending + success (no failure) → ``ci-pending``."""
    runs: list[dict[str, object]] = [
        {"conclusion": "success"},
        {"status": "in_progress", "conclusion": None},
    ]
    flags = _derive_flags(pr={"number": 1}, comments=[], check_runs=runs)
    assert "ci-pending" in flags
    assert "ci-red" not in flags
    assert "ci-green" not in flags


def test_derive_flags_no_ci_flag_when_no_check_runs() -> None:
    """Empty check-runs list → no ci-* flag (nothing to report about CI)."""
    flags = _derive_flags(pr={"number": 1}, comments=[], check_runs=[])
    assert not any(f.startswith("ci-") for f in flags)


def test_derive_flags_has_unresolved_comments_when_nonzero() -> None:
    """Any comment present → ``has-unresolved-comments``."""
    flags = _derive_flags(pr={"number": 1}, comments=[{"id": 1}], check_runs=[])
    assert "has-unresolved-comments" in flags


def test_derive_flags_no_unresolved_comments_flag_when_empty() -> None:
    """Empty comments list → no unresolved-comments flag."""
    flags = _derive_flags(pr={"number": 1}, comments=[], check_runs=[])
    assert "has-unresolved-comments" not in flags


def test_derive_flags_emits_merge_conflict_from_mergeable_state() -> None:
    """mergeable == CONFLICTING → ``merge-conflict``; other states don't emit it."""
    flags_conflict = _derive_flags(
        pr={"number": 1, "mergeable": "CONFLICTING"}, comments=[], check_runs=[]
    )
    assert "merge-conflict" in flags_conflict

    flags_clean = _derive_flags(
        pr={"number": 1, "mergeable": "MERGEABLE"}, comments=[], check_runs=[]
    )
    assert "merge-conflict" not in flags_clean
