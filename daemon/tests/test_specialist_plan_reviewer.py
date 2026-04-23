"""Tests for :class:`PlanReviewer`.

Deterministic context assembly is the core contract — the specialist is a
workflow-style wrapper that pre-loads plan files + the branch diff, then
hands a single assembled prompt to the brain. These tests use a real
``git init`` tmp_path so the subprocess plumbing is exercised end-to-end
without touching the live repo or any external service.

One integration-marker test at the bottom exercises the hybrid against a
real :class:`ClaudeSDKBrain` via ``ClaudeSDKBrain.with_tools(...)``; gated
behind ``REACHY_DUCKY_RUN_INTEGRATION=1`` the same way Task 2.3's
:mod:`test_brain_claude_integration` is gated.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_daemon.specialists.plan_reviewer import PlanReviewer
from reachy_ducky_daemon.specialists.redaction import RedactionError
from reachy_ducky_protocol.messages import SpecialistResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(*args: str, cwd: Path) -> None:
    """Run a git command inside ``cwd`` with noisy output suppressed.

    Uses list-form args (never ``shell=True``) and ``check=True`` so a
    fixture-setup failure surfaces as a test error, not a silent skip.
    """
    subprocess.run(
        list(args),
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _init_repo(root: Path) -> None:
    """Initialise a git repo under ``root`` with a deterministic identity.

    Sets a known user name/email via per-repo config so ``git commit``
    doesn't error in environments that lack a global identity (e.g. CI).
    """
    _run("git", "init", "-b", "main", cwd=root)
    _run("git", "config", "user.email", "test@example.com", cwd=root)
    _run("git", "config", "user.name", "Test User", cwd=root)
    _run("git", "config", "commit.gpgsign", "false", cwd=root)


def _commit(root: Path, message: str) -> None:
    """Stage everything and create a commit inside ``root``."""
    _run("git", "add", "-A", cwd=root)
    _run("git", "commit", "-m", message, cwd=root)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo_with_plan_and_drift(tmp_path: Path) -> Path:
    """Bootstrap a repo with one plan on ``main`` and a drifting branch.

    Shape:
        * ``main`` contains ``docs/plans/foo.md`` (title: ``# Plan Foo``).
        * A ``feat-drift`` branch adds a new file ``src/new_feature.py``
          that the plan never mentioned — the drift the specialist exists
          to detect.

    Returns the repo root; the checked-out HEAD is ``feat-drift``.
    """
    _init_repo(tmp_path)
    plans_dir = tmp_path / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "foo.md").write_text("# Plan Foo\n\nBuild a foo subsystem.\n")
    _commit(tmp_path, "initial plan")

    _run("git", "checkout", "-b", "feat-drift", cwd=tmp_path)
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "new_feature.py").write_text("def new_feature() -> None:\n    pass\n")
    _commit(tmp_path, "add drift")
    return tmp_path


@pytest.fixture
def repo_with_multiple_plans(tmp_path: Path) -> Path:
    """Bootstrap a repo where several plan files exist on ``main`` concurrently.

    Covers the conventional locations: ``docs/plans/**/*.md`` (nested),
    ``specs/**/*.md``, ``AGENTS.md`` at the root, and a ``*.plan.md`` at
    the root. A drifting branch adds a single new file so the diff is
    populated.
    """
    _init_repo(tmp_path)
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "alpha.md").write_text("# Plan Alpha\nalpha body\n")
    (tmp_path / "docs" / "plans" / "nested").mkdir()
    (tmp_path / "docs" / "plans" / "nested" / "beta.md").write_text(
        "# Plan Beta\nbeta body\n",
    )
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "gamma.md").write_text("# Plan Gamma\ngamma body\n")
    (tmp_path / "AGENTS.md").write_text("# Plan Delta\ndelta body\n")
    (tmp_path / "epsilon.plan.md").write_text("# Plan Epsilon\nepsilon body\n")
    _commit(tmp_path, "all the plans")

    _run("git", "checkout", "-b", "feat", cwd=tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "added.py").write_text("x = 1\n")
    _commit(tmp_path, "add drift")
    return tmp_path


@pytest.fixture
def repo_on_main_with_uncommitted(tmp_path: Path) -> Path:
    """Bootstrap a repo sitting on ``main`` with uncommitted working-tree changes.

    Exercises the fallback path: when HEAD is ``main`` there's no
    ``main...HEAD`` diff, so the specialist falls back to
    ``git diff`` (working-tree vs HEAD). The uncommitted change must
    modify a *tracked* file — plain ``git diff`` ignores untracked
    additions by default, so a brand-new file wouldn't exercise the
    fallback. Modifying ``docs/plans/foo.md`` itself mirrors a common
    real-world scenario: the user edits the plan and the diff shows
    that edit.
    """
    _init_repo(tmp_path)
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    plan_path = tmp_path / "docs" / "plans" / "foo.md"
    plan_path.write_text("# Plan Foo\n\nbody\n")
    _commit(tmp_path, "plan")

    # Still on main; mutate a tracked file so ``git diff`` has output.
    plan_path.write_text("# Plan Foo\n\nbody\nUNCOMMITTED_EDIT\n")
    return tmp_path


@pytest.fixture
def repo_without_plans(tmp_path: Path) -> Path:
    """Bootstrap a repo with no plan/spec files anywhere.

    The specialist must still return a :class:`SpecialistResponse`; the
    absence of plans is surfaced to the brain as diagnostic context in
    the assembled prompt.
    """
    _init_repo(tmp_path)
    (tmp_path / "README.md").write_text("not a plan\n")
    _commit(tmp_path, "initial")

    _run("git", "checkout", "-b", "feat", cwd=tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "added.py").write_text("x = 1\n")
    _commit(tmp_path, "drift")
    return tmp_path


# ---------------------------------------------------------------------------
# Deterministic prompt-assembly tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assembled_prompt_contains_plan_diff_and_directive(
    repo_with_plan_and_drift: Path,
) -> None:
    """Assert the composed prompt contains plan title + diff marker + directive."""
    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=repo_with_plan_and_drift)

    await reviewer.review()

    assert len(brain.calls) == 1
    prompt = brain.calls[-1].user_utterance
    assert "# Plan Foo" in prompt
    # Diff content: the new file's path must surface in the textual diff.
    assert "src/new_feature.py" in prompt
    # The drift-only directive is mandatory (used to constrain the brain's
    # tone / scope). A substring assertion is intentional — exact wording
    # is allowed to evolve, the anchor "Report drift" is the contract.
    assert "Report drift" in prompt


@pytest.mark.asyncio
async def test_assembled_prompt_includes_every_plan_file(
    repo_with_multiple_plans: Path,
) -> None:
    """Every discovered plan file's title is present in the assembled prompt."""
    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=repo_with_multiple_plans)

    await reviewer.review()

    prompt = brain.calls[-1].user_utterance
    for title in (
        "# Plan Alpha",
        "# Plan Beta",
        "# Plan Gamma",
        "# Plan Delta",
        "# Plan Epsilon",
    ):
        assert title in prompt


@pytest.mark.asyncio
async def test_main_branch_fallback_includes_uncommitted_diff(
    repo_on_main_with_uncommitted: Path,
) -> None:
    """On ``main``, falls back to working-tree-vs-HEAD diff.

    ``main...HEAD`` is empty (HEAD *is* main), so the fallback is what
    surfaces the uncommitted change in the assembled prompt.
    """
    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=repo_on_main_with_uncommitted)

    await reviewer.review()

    prompt = brain.calls[-1].user_utterance
    assert "UNCOMMITTED_EDIT" in prompt


@pytest.mark.asyncio
async def test_review_handles_repo_with_no_plans(
    repo_without_plans: Path,
) -> None:
    """A repo with zero plan files still yields a ``SpecialistResponse``.

    The brain must be invoked exactly once so the user gets a narrative
    response (even if the narrative is "no plan to compare against").
    The diagnostic must be explicit in the prompt so the brain knows
    *why* the plan section is empty.
    """
    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=repo_without_plans)

    response = await reviewer.review()

    assert isinstance(response, SpecialistResponse)
    assert len(brain.calls) == 1
    prompt = brain.calls[-1].user_utterance
    assert "no plan" in prompt.lower()


# ---------------------------------------------------------------------------
# Response-shape + side-effect verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_returns_specialist_response_with_brain_text(
    repo_with_plan_and_drift: Path,
) -> None:
    """``review()`` wraps the brain's text in a ``SpecialistResponse``.

    ``MockBrain.query`` echoes the prompt with a ``[mock]`` prefix, so
    the response's ``summary`` must match that echo exactly — this pins
    down both the wrapping and the field mapping.
    """
    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=repo_with_plan_and_drift)

    response = await reviewer.review()

    assert response.name == "plan-reviewer"
    assert response.summary == f"[mock] {brain.calls[-1].user_utterance}"


@pytest.mark.asyncio
async def test_review_invokes_brain_exactly_once(
    repo_with_plan_and_drift: Path,
) -> None:
    """Each ``review()`` call awaits the brain exactly once — no retries, no fan-out."""
    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=repo_with_plan_and_drift)

    await reviewer.review()

    assert len(brain.calls) == 1


# ---------------------------------------------------------------------------
# Integration (gated)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_plan_reviewer_live_claude(repo_with_plan_and_drift: Path) -> None:
    """End-to-end against live Claude via ``ClaudeSDKBrain.with_tools(...)``.

    Gated on ``REACHY_DUCKY_RUN_INTEGRATION=1`` — this is the only test in
    this module that requires an authenticated SDK. Pattern mirrors
    Task 2.3's integration test.
    """
    if not os.environ.get("REACHY_DUCKY_RUN_INTEGRATION"):
        pytest.skip("set REACHY_DUCKY_RUN_INTEGRATION=1 to run")

    # Import here so the module still imports cleanly in environments
    # where the SDK's tool-mode dependencies aren't fully configured.
    from reachy_ducky_daemon.brain.claude_sdk import ClaudeSDKBrain

    memory_root = repo_with_plan_and_drift / ".memory"
    memory_root.mkdir()
    brain = ClaudeSDKBrain.with_tools(
        cwd=repo_with_plan_and_drift,
        memory_root=memory_root,
    )
    reviewer = PlanReviewer(brain=brain, repo=repo_with_plan_and_drift)

    response = await reviewer.review()

    assert response.name == "plan-reviewer"
    assert response.summary  # non-empty


# ---------------------------------------------------------------------------
# Redaction integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_reviewer_redacts_prompt_before_brain_query(
    repo_with_plan_and_drift: Path,
) -> None:
    """Prompt reaching the brain is the redacted version; rule ids flow to flags."""
    # Inject a sensitive-looking token into the plan so we can observe it
    # being scrubbed. Fresh tmp_path per test — safe to mutate.
    plan_path = repo_with_plan_and_drift / "docs" / "plans" / "foo.md"
    plan_path.write_text(plan_path.read_text() + "\nsensitive token here\n")

    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=repo_with_plan_and_drift)

    def _fake_redact(text: str) -> tuple[str, list[str]]:
        return text.replace("sensitive token here", "[REDACTED:fake-rule]"), ["fake-rule"]

    with patch(
        "reachy_ducky_daemon.specialists.plan_reviewer.redact",
        side_effect=_fake_redact,
    ):
        response = await reviewer.review()

    assert len(brain.calls) == 1
    prompt = brain.calls[0].user_utterance
    assert "sensitive token here" not in prompt
    assert "[REDACTED:fake-rule]" in prompt
    assert "redacted:fake-rule" in response.flags


@pytest.mark.asyncio
async def test_plan_reviewer_aborts_on_redaction_failure(
    repo_with_plan_and_drift: Path,
) -> None:
    """RedactionError → 200 SpecialistResponse with redaction-failed flag, no brain call."""
    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=repo_with_plan_and_drift)

    with patch(
        "reachy_ducky_daemon.specialists.plan_reviewer.redact",
        side_effect=RedactionError("gitleaks binary not found"),
    ):
        response = await reviewer.review()

    assert response.name == "plan-reviewer"
    assert "redaction-failed" in response.flags
    assert "gitleaks binary not found" in response.summary
    assert "abort" in response.summary.lower() or "unavailable" in response.summary.lower()
    assert len(brain.calls) == 0, "brain.query must not fire when redaction fails"
