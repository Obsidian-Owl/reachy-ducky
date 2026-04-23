"""Tests for :class:`PRReviewer`.

Follows the same shape as ``test_specialist_plan_reviewer.py`` — subprocess
calls are mocked at the ``subprocess.run`` boundary (rather than running
real ``gh``, which would need network + auth + a live PR). Canned ``gh``
outputs live under ``daemon/tests/fixtures/gh_*.json``.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_daemon.specialists.pr_reviewer import (
    _GH_TIMEOUT_SECONDS,
    PRReviewer,
    _assemble_diagnostic_prompt,
    _assemble_prompt,
    _current_branch,
    _derive_flags,
    _fetch_check_runs,
    _fetch_diff,
    _fetch_pr_metadata,
    _fetch_review_comments,
    _find_pr_for_branch,
    _run_gh,
)
from reachy_ducky_protocol.messages import SpecialistResponse


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


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def test_assemble_prompt_includes_title_body_diff_comments_ci() -> None:
    """Happy-path prompt contains PR title, body, diff, comments, CI, and directive."""
    pr: dict[str, object] = {
        "number": 42,
        "title": "feat: add retry logic",
        "body": "Closes #15. Adds exponential backoff to the upload path.",
        "headRefName": "feat-retry",
        "baseRefName": "main",
        "url": "https://github.com/Obsidian-Owl/reachy-ducky/pull/42",
    }
    comments: list[dict[str, object]] = [
        {
            "user": {"login": "augment-code[bot]"},
            "path": "src/upload.py",
            "line": 42,
            "body": "no jitter term on the retry",
        }
    ]
    check_runs: list[dict[str, object]] = [
        {"name": "mypy", "status": "completed", "conclusion": "success"}
    ]

    prompt = _assemble_prompt(
        pr=pr,
        diff="diff --git a/src/upload.py\n+x = 2\n",
        comments=comments,
        check_runs=check_runs,
    )

    # PR metadata anchors.
    assert "#42" in prompt
    assert "feat: add retry logic" in prompt
    assert "Closes #15" in prompt
    assert "feat-retry" in prompt
    assert "main" in prompt
    # Diff anchor.
    assert "+x = 2" in prompt
    # Comments: author login + file:line + body all present for referential precision.
    assert "augment-code[bot]" in prompt
    assert "src/upload.py" in prompt
    assert "42" in prompt  # the line number (also the PR number; substring is fine)
    assert "no jitter term" in prompt
    # CI section: check name + conclusion.
    assert "mypy" in prompt
    assert "success" in prompt
    # Stable section headers so the brain can anchor attention.
    assert "=== PR BODY ===" in prompt
    assert "=== DIFF ===" in prompt
    assert "=== REVIEW COMMENTS ===" in prompt
    assert "=== CI / CHECK RUNS ===" in prompt
    assert "=== TASK ===" in prompt
    # Directive anchor.
    assert "synthes" in prompt.lower() or "digest" in prompt.lower()


def test_assemble_prompt_handles_empty_optional_sections() -> None:
    """Empty diff / no comments / no check-runs still produce a valid prompt."""
    pr: dict[str, object] = {
        "number": 1,
        "title": "trivial",
        "body": "",
        "headRefName": "f",
        "baseRefName": "main",
    }
    prompt = _assemble_prompt(pr=pr, diff="", comments=[], check_runs=[])
    # Every section header is still present, with a marker explaining the emptiness.
    assert "=== DIFF ===" in prompt
    assert "(empty" in prompt.lower()
    assert "=== REVIEW COMMENTS ===" in prompt
    assert "(no line-level" in prompt.lower() or "no comments" in prompt.lower()
    assert "=== CI / CHECK RUNS ===" in prompt
    assert "no check" in prompt.lower()


def test_assemble_prompt_surfaces_diff_fetch_error() -> None:
    """Fetch failure on the diff surfaces as a diagnostic, not silent (empty diff).

    An empty diff and a failed diff fetch look identical to the brain if
    we pass ``diff=""`` with no explanatory context — dangerous for the
    "risk call" (brain might say "no changes so safe" on a PR whose diff
    we couldn't read). The diagnostic kwarg lets the brain distinguish.
    """
    pr: dict[str, object] = {
        "number": 1,
        "title": "t",
        "body": "",
        "headRefName": "f",
        "baseRefName": "main",
    }
    prompt = _assemble_prompt(
        pr=pr,
        diff="",
        comments=[],
        check_runs=[],
        diff_error="gh pr diff 1 failed: authentication required",
    )
    # Diagnostic lands inside the DIFF section so the brain anchors it.
    diff_section = prompt.split("=== DIFF ===", 1)[1].split("=== REVIEW COMMENTS ===", 1)[0]
    assert "diagnostic" in diff_section.lower()
    assert "authentication required" in diff_section


def test_assemble_prompt_surfaces_comments_fetch_error() -> None:
    """Fetch failure on review comments surfaces inside the comments section."""
    pr: dict[str, object] = {
        "number": 1,
        "title": "t",
        "body": "",
        "headRefName": "f",
        "baseRefName": "main",
    }
    prompt = _assemble_prompt(
        pr=pr,
        diff="",
        comments=[],
        check_runs=[],
        comments_error="gh api /repos/o/r/pulls/1/comments failed: 404 Not Found",
    )
    comments_section = prompt.split("=== REVIEW COMMENTS ===", 1)[1].split(
        "=== CI / CHECK RUNS ===", 1
    )[0]
    assert "diagnostic" in comments_section.lower()
    assert "404 Not Found" in comments_section


def test_assemble_prompt_surfaces_check_runs_fetch_error() -> None:
    """Fetch failure on check-runs surfaces inside the CI section — not silent.

    Silent handling is the correctness hazard Augment flagged: an
    authentication failure on ``/commits/<sha>/check-runs`` would leave
    ``check_runs=[]`` and ``_derive_flags`` emits no ``ci-*`` flag at
    all — menubar shows neutral on a PR whose CI state we didn't see.
    """
    pr: dict[str, object] = {
        "number": 1,
        "title": "t",
        "body": "",
        "headRefName": "f",
        "baseRefName": "main",
    }
    prompt = _assemble_prompt(
        pr=pr,
        diff="",
        comments=[],
        check_runs=[],
        check_runs_error="gh api /repos/o/r/commits/abc/check-runs failed: 403",
    )
    ci_section = prompt.split("=== CI / CHECK RUNS ===", 1)[1].split("=== TASK ===", 1)[0]
    assert "diagnostic" in ci_section.lower()
    assert "403" in ci_section


def test_assemble_diagnostic_prompt_explains_no_pr() -> None:
    """Diagnostic prompt names the branch, says no PR, asks brain to investigate."""
    prompt = _assemble_diagnostic_prompt(
        branch="feat-orphan",
        branch_error=None,
        find_error=None,
    )
    assert "feat-orphan" in prompt
    assert "no open pr" in prompt.lower() or "no pr" in prompt.lower()
    # Directive asks brain to investigate with tools.
    assert "investigate" in prompt.lower() or "check" in prompt.lower()
    assert "=== TASK ===" in prompt


def test_assemble_diagnostic_prompt_surfaces_sub_diagnostics() -> None:
    """Sub-diagnostics (branch_error, find_error) show up so the brain has full context."""
    prompt = _assemble_diagnostic_prompt(
        branch="unknown",
        branch_error="git rev-parse failed: fatal: not a git repository",
        find_error="gh pr list --head unknown failed: authentication required",
    )
    assert "not a git repository" in prompt
    assert "authentication required" in prompt


# ---------------------------------------------------------------------------
# PRReviewer orchestrator — end-to-end (subprocess mocked, brain mocked)
# ---------------------------------------------------------------------------


def _ok(stdout: str) -> subprocess.CompletedProcess[str]:
    """Shorthand for a successful ``CompletedProcess`` with canned stdout."""
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _mock_by_argv(
    returns: dict[tuple[str, ...], subprocess.CompletedProcess[str]],
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Build a ``subprocess.run`` side_effect that dispatches by argv prefix.

    Longer patterns take precedence over shorter ones — important for
    distinguishing ``gh api /repos/.../pulls/.../comments`` from
    ``gh api /repos/.../commits/.../check-runs``.

    A default no-findings ``gitleaks stdin`` response is merged in so
    existing tests (written before redaction wiring landed) dispatch
    cleanly without touching each case. Tests that want to observe
    redaction-specific behavior can override by supplying their own
    ``("gitleaks", "stdin")`` entry, or by patching
    ``pr_reviewer.redact`` directly.
    """
    defaults: dict[tuple[str, ...], subprocess.CompletedProcess[str]] = {
        ("gitleaks", "stdin"): _ok("[]"),
    }
    merged = {**defaults, **returns}
    # Sort keys by length descending so the most specific match wins.
    ordered = sorted(merged.items(), key=lambda kv: -len(kv[0]))

    def _side_effect(
        argv: list[str], *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        key = tuple(argv)
        for pat, proc in ordered:
            if key[: len(pat)] == pat:
                return proc
        raise AssertionError(f"unexpected argv: {argv}")

    return _side_effect


@pytest.mark.asyncio
async def test_review_explicit_pr_number_happy_path(tmp_path: Path) -> None:
    """Explicit pr_number → fetch all surfaces, assemble prompt, query brain once."""
    pr_view = _FIXTURES / "gh_pr_view_happy.json"
    comments = _FIXTURES / "gh_api_comments.json"
    check_runs = _FIXTURES / "gh_api_check_runs.json"

    side_effect = _mock_by_argv(
        {
            ("gh", "pr", "view"): _ok(pr_view.read_text()),
            ("gh", "pr", "diff"): _ok("diff --git a/src/x.py\n+x = 2\n"),
            (
                "gh",
                "api",
                "/repos/Obsidian-Owl/reachy-ducky/pulls/42/comments",
            ): _ok(comments.read_text()),
            (
                "gh",
                "api",
                "/repos/Obsidian-Owl/reachy-ducky/commits/abc123def456/check-runs",
            ): _ok(check_runs.read_text()),
        }
    )

    brain = MockBrain()
    reviewer = PRReviewer(
        brain=brain,
        repo=tmp_path,
        owner="Obsidian-Owl",
        repo_name="reachy-ducky",
    )
    with patch("subprocess.run", side_effect=side_effect):
        response = await reviewer.review(pr_number=42)

    assert isinstance(response, SpecialistResponse)
    assert response.name == "pr-reviewer"
    # Exactly one brain call — Pattern A contract.
    assert len(brain.calls) == 1
    prompt = brain.calls[0].user_utterance
    assert "feat: add retry logic" in prompt
    assert "+x = 2" in prompt
    assert "augment-code[bot]" in prompt
    # Fixture has mypy failing → ci-red wins over pytest pending.
    assert "ci-red" in response.flags
    assert "has-unresolved-comments" in response.flags


@pytest.mark.asyncio
async def test_review_auto_detect_from_current_branch(tmp_path: Path) -> None:
    """No pr_number → git rev-parse + gh pr list → resolve → same happy fetch."""
    pr_view = _FIXTURES / "gh_pr_view_happy.json"

    side_effect = _mock_by_argv(
        {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): _ok("feat-retry\n"),
            ("gh", "pr", "list"): _ok('[{"number": 42}]'),
            ("gh", "pr", "view"): _ok(pr_view.read_text()),
            ("gh", "pr", "diff"): _ok(""),
            (
                "gh",
                "api",
                "/repos/Obsidian-Owl/reachy-ducky/pulls/42/comments",
            ): _ok("[]"),
            (
                "gh",
                "api",
                "/repos/Obsidian-Owl/reachy-ducky/commits/abc123def456/check-runs",
            ): _ok('{"total_count": 0, "check_runs": []}'),
        }
    )

    brain = MockBrain()
    reviewer = PRReviewer(
        brain=brain,
        repo=tmp_path,
        owner="Obsidian-Owl",
        repo_name="reachy-ducky",
    )
    with patch("subprocess.run", side_effect=side_effect):
        response = await reviewer.review(pr_number=None)

    assert response.name == "pr-reviewer"
    assert len(brain.calls) == 1
    assert "feat: add retry logic" in brain.calls[0].user_utterance
    # Empty comments + empty check-runs — no ci-* flag, no unresolved-comments.
    assert not any(f.startswith("ci-") for f in response.flags)
    assert "has-unresolved-comments" not in response.flags


@pytest.mark.asyncio
async def test_review_graceful_fail_when_no_pr_for_branch(tmp_path: Path) -> None:
    """Auto-detect branch has no open PR → diagnostic prompt + no-pr-found flag."""
    side_effect = _mock_by_argv(
        {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): _ok("feat-orphan\n"),
            ("gh", "pr", "list"): _ok("[]"),
        }
    )

    brain = MockBrain()
    reviewer = PRReviewer(
        brain=brain,
        repo=tmp_path,
        owner="Obsidian-Owl",
        repo_name="reachy-ducky",
    )
    with patch("subprocess.run", side_effect=side_effect):
        response = await reviewer.review(pr_number=None)

    assert response.name == "pr-reviewer"
    assert "no-pr-found" in response.flags
    assert len(brain.calls) == 1
    prompt = brain.calls[0].user_utterance
    assert "feat-orphan" in prompt
    assert "no open pr" in prompt.lower() or "no pr" in prompt.lower()


@pytest.mark.asyncio
async def test_review_graceful_fail_surfaces_gh_lookup_error(tmp_path: Path) -> None:
    """gh pr list failing is distinct from "no PR" — error is in the diagnostic prompt."""
    side_effect = _mock_by_argv(
        {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): _ok("feat-x\n"),
            ("gh", "pr", "list"): subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="authentication required"
            ),
        }
    )

    brain = MockBrain()
    reviewer = PRReviewer(
        brain=brain,
        repo=tmp_path,
        owner="Obsidian-Owl",
        repo_name="reachy-ducky",
    )
    with patch("subprocess.run", side_effect=side_effect):
        response = await reviewer.review(pr_number=None)

    assert "no-pr-found" in response.flags
    prompt = brain.calls[0].user_utterance
    assert "authentication required" in prompt


@pytest.mark.asyncio
async def test_review_surfaces_diff_fetch_error_in_prompt(tmp_path: Path) -> None:
    """Even when PR metadata fetch succeeds, a failed gh pr diff must surface.

    Pins the behaviour Augment called out: fetch errors on diff/comments/
    CI must not be silently degraded into "empty" values — the brain must
    see the diagnostic so it can distinguish "nothing there" from
    "couldn't see what was there."
    """
    pr_view = _FIXTURES / "gh_pr_view_happy.json"

    side_effect = _mock_by_argv(
        {
            ("gh", "pr", "view"): _ok(pr_view.read_text()),
            # Diff fetch fails — auth-shaped error from gh.
            ("gh", "pr", "diff"): subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="authentication required"
            ),
            (
                "gh",
                "api",
                "/repos/Obsidian-Owl/reachy-ducky/pulls/42/comments",
            ): _ok("[]"),
            (
                "gh",
                "api",
                "/repos/Obsidian-Owl/reachy-ducky/commits/abc123def456/check-runs",
            ): _ok('{"total_count": 0, "check_runs": []}'),
        }
    )

    brain = MockBrain()
    reviewer = PRReviewer(
        brain=brain,
        repo=tmp_path,
        owner="Obsidian-Owl",
        repo_name="reachy-ducky",
    )
    with patch("subprocess.run", side_effect=side_effect):
        await reviewer.review(pr_number=42)

    prompt = brain.calls[0].user_utterance
    assert "authentication required" in prompt, (
        "fetch error on diff was silently swallowed — brain cannot distinguish "
        "'no changes' from 'could not read changes'"
    )


# ---------------------------------------------------------------------------
# Integration (gated) — live Claude + real GitHub against a stable closed PR
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pr_reviewer_live_claude(tmp_path: Path) -> None:
    """End-to-end smoke against real Claude + real GitHub, stable closed PR #46.

    Gated on ``REACHY_DUCKY_RUN_INTEGRATION=1`` — mirrors Task 2.3's
    integration test shape (``test_plan_reviewer_live_claude``).
    Targets ``Obsidian-Owl/reachy-ducky#46`` (closed pytest-asyncio bump;
    won't drift). If you move this test to a different PR, pick one that
    is closed/merged so the diff + comments surface stays deterministic.

    Prereqs on the runner:

    * ``gh`` CLI installed and authenticated (``GH_TOKEN`` or ``gh auth
      login``).
    * Claude Code CLI logged in so the Agent SDK inherits OAuth, or
      ``ANTHROPIC_API_KEY`` set (see design doc §5).
    """
    if not os.environ.get("REACHY_DUCKY_RUN_INTEGRATION"):
        pytest.skip("set REACHY_DUCKY_RUN_INTEGRATION=1 to run")

    from reachy_ducky_daemon.brain.claude_sdk import ClaudeSDKBrain

    # The integration target repo IS this checkout. ``gh`` infers the
    # upstream from the remote, and ``ClaudeSDKBrain.with_tools`` scopes
    # Read/Grep/Glob to ``cwd``.
    repo = Path.cwd()
    memory_root = tmp_path / "memory"
    memory_root.mkdir()

    brain = ClaudeSDKBrain.with_tools(
        cwd=repo,
        memory_root=memory_root,
        github_repo="Obsidian-Owl/reachy-ducky",
    )
    reviewer = PRReviewer(
        brain=brain,
        repo=repo,
        owner="Obsidian-Owl",
        repo_name="reachy-ducky",
    )
    response = await reviewer.review(pr_number=46)

    assert response.name == "pr-reviewer"
    assert response.summary  # brain returned *something* non-empty


# ---------------------------------------------------------------------------
# Redaction integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_reviewer_redacts_happy_path(tmp_path: Path) -> None:
    """Happy path: prompt the brain sees is the redacted version; rule ids flagged."""
    pr_view = _FIXTURES / "gh_pr_view_happy.json"
    comments = _FIXTURES / "gh_api_comments.json"
    check_runs = _FIXTURES / "gh_api_check_runs.json"

    side_effect = _mock_by_argv(
        {
            ("gh", "pr", "view"): _ok(pr_view.read_text()),
            ("gh", "pr", "diff"): _ok("diff context\nsensitive_token_here\nmore diff\n"),
            (
                "gh",
                "api",
                "/repos/Obsidian-Owl/reachy-ducky/pulls/42/comments",
            ): _ok(comments.read_text()),
            (
                "gh",
                "api",
                "/repos/Obsidian-Owl/reachy-ducky/commits/abc123def456/check-runs",
            ): _ok(check_runs.read_text()),
        }
    )

    def _fake_redact(text: str, *, cwd: Path) -> tuple[str, list[str]]:
        assert cwd == tmp_path

        return text.replace("sensitive_token_here", "[REDACTED:fake-rule]"), ["fake-rule"]

    brain = MockBrain()
    reviewer = PRReviewer(
        brain=brain,
        repo=tmp_path,
        owner="Obsidian-Owl",
        repo_name="reachy-ducky",
    )
    with (
        patch("subprocess.run", side_effect=side_effect),
        patch(
            "reachy_ducky_daemon.specialists.pr_reviewer.redact",
            side_effect=_fake_redact,
        ),
    ):
        response = await reviewer.review(pr_number=42)

    assert "sensitive_token_here" not in brain.calls[0].user_utterance
    assert "[REDACTED:fake-rule]" in brain.calls[0].user_utterance
    assert "redacted:fake-rule" in response.flags
    # Existing derived flags preserved — flag list is a union, not a replacement.
    assert "ci-red" in response.flags
    assert "has-unresolved-comments" in response.flags


@pytest.mark.asyncio
async def test_pr_reviewer_redacts_diagnostic_path(tmp_path: Path) -> None:
    """Diagnostic (no-PR-found) path also redacts.

    branch_error / find_error / stderr strings can legitimately embed
    credentials (auth URLs with tokens, etc.), so the fail-closed
    posture must cover the diagnostic path too.
    """
    side_effect = _mock_by_argv(
        {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): _ok("feat-x\n"),
            # gh pr list fails with a stderr that embeds a credential-ish URL.
            ("gh", "pr", "list"): subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="remote: url https://x:sensitive_token_here@github.com/...",
            ),
        }
    )

    def _fake_redact(text: str, *, cwd: Path) -> tuple[str, list[str]]:
        assert cwd == tmp_path

        return text.replace("sensitive_token_here", "[REDACTED:fake-rule]"), ["fake-rule"]

    brain = MockBrain()
    reviewer = PRReviewer(
        brain=brain,
        repo=tmp_path,
        owner="Obsidian-Owl",
        repo_name="reachy-ducky",
    )
    with (
        patch("subprocess.run", side_effect=side_effect),
        patch(
            "reachy_ducky_daemon.specialists.pr_reviewer.redact",
            side_effect=_fake_redact,
        ),
    ):
        response = await reviewer.review(pr_number=None)

    assert "sensitive_token_here" not in brain.calls[0].user_utterance
    assert "[REDACTED:fake-rule]" in brain.calls[0].user_utterance
    assert "redacted:fake-rule" in response.flags
    assert "no-pr-found" in response.flags


@pytest.mark.asyncio
async def test_pr_reviewer_aborts_on_redaction_failure(tmp_path: Path) -> None:
    """RedactionError on the happy path → 200 response, redaction-failed flag, no brain call."""
    from reachy_ducky_daemon.specialists.redaction import RedactionError

    pr_view = _FIXTURES / "gh_pr_view_happy.json"
    side_effect = _mock_by_argv(
        {
            ("gh", "pr", "view"): _ok(pr_view.read_text()),
            ("gh", "pr", "diff"): _ok(""),
            ("gh", "api"): _ok("[]"),
        }
    )

    brain = MockBrain()
    reviewer = PRReviewer(
        brain=brain,
        repo=tmp_path,
        owner="Obsidian-Owl",
        repo_name="reachy-ducky",
    )
    with (
        patch("subprocess.run", side_effect=side_effect),
        patch(
            "reachy_ducky_daemon.specialists.pr_reviewer.redact",
            side_effect=RedactionError("gitleaks timeout after 30.0s"),
        ),
    ):
        response = await reviewer.review(pr_number=42)

    assert response.name == "pr-reviewer"
    assert "redaction-failed" in response.flags
    assert "timeout" in response.summary.lower()
    assert len(brain.calls) == 0, "brain.query must not fire when redaction fails"
