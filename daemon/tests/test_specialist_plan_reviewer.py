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
    # Banner is gated on fallback_err is not None — the on-main path
    # never attempted a merge-base diff, so no banner appears.
    assert "(fallback:" not in prompt


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

    def _fake_redact(text: str, *, cwd: Path) -> tuple[str, list[str]]:
        assert cwd == repo_with_plan_and_drift

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


# ---------------------------------------------------------------------------
# Unreadable-plan diagnostic surfacing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_surfaces_unreadable_plan_diagnostic(
    tmp_path: Path,
) -> None:
    """A plan file that can't be read (non-UTF-8) surfaces under UNREADABLE PLANS.

    Phase-A parity: _current_branch and _capture_diff already surface
    errors as '(diagnostic: ...)' in the prompt. _collect_plans
    previously swallowed OSError/UnicodeDecodeError silently; now the
    brain sees 'these files exist but we couldn't read them' instead of
    'these files never existed'.
    """
    _init_repo(tmp_path)
    plans_dir = tmp_path / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    # A legitimate, readable plan
    (plans_dir / "ok.md").write_text("# OK Plan\nreadable body\n")
    # A binary blob that isn't valid UTF-8
    (plans_dir / "bad.md").write_bytes(b"\xff\xfe\x00\x00not utf-8")
    _commit(tmp_path, "plans")

    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=tmp_path)
    await reviewer.review()

    prompt = brain.calls[-1].user_utterance
    # Readable plan still present
    assert "# OK Plan" in prompt
    # New section header present
    assert "=== UNREADABLE PLANS ===" in prompt
    # Failing file's path is named in the diagnostic
    unreadable_section = prompt.split("=== UNREADABLE PLANS ===", 1)[1]
    assert "bad.md" in unreadable_section
    # Diagnostic mentions the failure mode
    assert "utf-8" in unreadable_section.lower() or "decode" in unreadable_section.lower()


@pytest.mark.asyncio
async def test_prompt_has_no_contradiction_when_all_plans_unreadable(
    tmp_path: Path,
) -> None:
    """When every discovered plan is unreadable, the PLANS section must not
    claim 'no plan or spec files discovered' — that contradicts the
    UNREADABLE PLANS section which names specific discovered-but-unreadable
    files.
    """
    _init_repo(tmp_path)
    plans_dir = tmp_path / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    # Two non-UTF-8 blobs — no readable plan at all.
    (plans_dir / "bad1.md").write_bytes(b"\xff\xfe\x00\x00not utf-8 one")
    (plans_dir / "bad2.md").write_bytes(b"\xff\xfe\x00\x00not utf-8 two")
    _commit(tmp_path, "only-bad-plans")

    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=tmp_path)
    await reviewer.review()

    prompt = brain.calls[-1].user_utterance
    # Must surface both filenames as unreadable diagnostics.
    assert "=== UNREADABLE PLANS ===" in prompt
    assert "bad1.md" in prompt
    assert "bad2.md" in prompt
    # Must NOT claim nothing was discovered — that contradicts the list above.
    assert "no plan or spec files discovered" not in prompt
    # Must still signal the empty-readable state somehow, pointing to the
    # UNREADABLE PLANS section.
    plans_section = prompt.split("=== PLANS ===", 1)[1].split("=== UNREADABLE PLANS ===", 1)[0]
    assert "no readable plan" in plans_section.lower(), (
        f"PLANS section should say 'no readable plan...'; got: {plans_section!r}"
    )


@pytest.mark.asyncio
async def test_review_omits_unreadable_section_when_all_plans_load(
    tmp_path: Path,
) -> None:
    """When every plan reads cleanly, the UNREADABLE PLANS section is absent.

    Avoids cluttering the prompt with an empty diagnostic header.
    """
    _init_repo(tmp_path)
    plans_dir = tmp_path / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "ok.md").write_text("# OK Plan\nreadable body\n")
    _commit(tmp_path, "plans")

    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=tmp_path)
    await reviewer.review()

    prompt = brain.calls[-1].user_utterance
    assert "# OK Plan" in prompt
    # No empty section header
    assert "=== UNREADABLE PLANS ===" not in prompt


@pytest.mark.asyncio
async def test_review_oserror_diagnostic_omits_absolute_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OSError diagnostics use exc.strerror, not str(exc) — no absolute path leak.

    str(OSError) embeds the full filesystem path, e.g.
    'OSError: [Errno 13] Permission denied: /Users/.../docs/plans/x.md'.
    The brain already has the relative path via the '--- {rel} ---'
    section header; surfacing the absolute path again is a needless
    leak of usernames / client-project names through the prompt.
    """
    _init_repo(tmp_path)
    plans_dir = tmp_path / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    secret = plans_dir / "x.md"
    secret.write_text("# X\n")
    _commit(tmp_path, "plans")

    # Inject a synthetic OSError via monkeypatch: chmod 0 is unreliable
    # across platforms (owner reads can still succeed on some setups),
    # so swap Path.read_text for an implementation that raises OSError
    # with the absolute path embedded — mirroring real PermissionError
    # behaviour on POSIX.
    real_read_text = Path.read_text

    def fake_read_text(
        self: Path,
        *args: object,
        **kwargs: object,
    ) -> str:
        if self == secret:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=tmp_path)
    await reviewer.review()

    prompt = brain.calls[-1].user_utterance
    # The relative path appears in the section header (expected).
    assert "x.md" in prompt
    # The ABSOLUTE path must NOT appear — that's the leak we're closing.
    assert str(tmp_path) not in prompt


# ---------------------------------------------------------------------------
# Size caps — per-file + total-budget (#2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_file_cap_truncates_individual_plans(tmp_path: Path) -> None:
    """Plans longer than max_plan_chars get a truncation marker."""
    _init_repo(tmp_path)
    plans_dir = tmp_path / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    big = "x" * 120_000
    (plans_dir / "big.md").write_text(big)
    _commit(tmp_path, "big plan")

    brain = MockBrain()
    reviewer = PlanReviewer(
        brain=brain,
        repo=tmp_path,
        max_plan_chars=50_000,
        max_total_plan_chars=200_000,
    )
    await reviewer.review()

    prompt = brain.calls[-1].user_utterance
    # Per-file truncation marker present with the exact elided count.
    assert "[... truncated: 70000 chars elided ...]" in prompt
    # The full 120k body must NOT appear; allow modest overhead for
    # other prompt sections that don't contain 'x'.
    assert prompt.count("x") <= 60_000  # 50k truncated body + small slack


@pytest.mark.asyncio
async def test_total_cap_drops_remaining_plans(tmp_path: Path) -> None:
    """Plans beyond max_total_plan_chars are replaced by an omission marker.

    Asserts the full policy, not just the marker's presence:

    * Insertion order of the plans that *do* land is preserved
      (``p0 < p1 < p2``).
    * The last plan is omitted (``p4.md`` does not appear).
    * The marker names the exact remaining count (``"2 plans omitted"``;
      grammatically plural since two are dropped).
    * The marker sits *after* the last included plan, not before —
      proving the early-exit ordering rather than a stray substring.
    """
    _init_repo(tmp_path)
    plans_dir = tmp_path / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    # 5 plans × 50k each = 250k, exceeds 200k total cap.
    for i in range(5):
        (plans_dir / f"p{i}.md").write_text("y" * 50_000)
    _commit(tmp_path, "many plans")

    brain = MockBrain()
    reviewer = PlanReviewer(
        brain=brain,
        repo=tmp_path,
        max_plan_chars=60_000,
        max_total_plan_chars=200_000,
    )
    await reviewer.review()

    prompt = brain.calls[-1].user_utterance
    # At least the first plan landed and the budget number is named.
    assert "p0.md" in prompt
    assert "200000" in prompt
    # Ordering property: insertion order preserved for included plans.
    assert prompt.index("p0.md") < prompt.index("p1.md") < prompt.index("p2.md")
    # Last-plan-dropped property: p4 is past the budget, so it never appears.
    assert "p4.md" not in prompt
    # Exact count in marker — 5 plans in, 3 land, 2 are omitted.
    assert "2 plans omitted" in prompt
    # Position property: marker follows the last included plan, not precedes it.
    assert prompt.index("p2.md") < prompt.index("plans omitted")


def test_caps_have_sensible_defaults(tmp_path: Path) -> None:
    """Defaults: max_plan_chars=50_000, max_total_plan_chars=200_000.

    Pins the defaults so a future change is deliberate. Constructor is
    keyword-only on these kwargs.
    """
    reviewer = PlanReviewer(brain=MockBrain(), repo=tmp_path)
    assert reviewer._max_plan_chars == 50_000  # noqa: SLF001
    assert reviewer._max_total_plan_chars == 200_000  # noqa: SLF001


# ---------------------------------------------------------------------------
# Merge-base fallback banner (#3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capture_diff_falls_back_when_main_ref_absent(tmp_path: Path) -> None:
    """Feature branch but no 'main' ref → fall back to working-tree-vs-HEAD.

    Banner: the fallback diff text starts with a one-line marker telling
    the brain it came from the fallback path, not the merge-base diff.
    """
    # Init with default branch 'feat-alone' instead of 'main'.
    _run("git", "init", "-b", "feat-alone", cwd=tmp_path)
    _run("git", "config", "user.email", "test@example.com", cwd=tmp_path)
    _run("git", "config", "user.name", "Test User", cwd=tmp_path)
    _run("git", "config", "commit.gpgsign", "false", cwd=tmp_path)
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "foo.md").write_text("# Plan Foo\n")
    _commit(tmp_path, "initial")
    # Make an uncommitted edit so `git diff` has output.
    (tmp_path / "docs" / "plans" / "foo.md").write_text("# Plan Foo\nUNCOMMITTED_EDIT\n")

    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=tmp_path)
    await reviewer.review()

    prompt = brain.calls[-1].user_utterance
    # Fallback engaged — uncommitted edit surfaces.
    assert "UNCOMMITTED_EDIT" in prompt
    diff_section = prompt.split("=== DIFF ===", 1)[1]
    diff_lines = [line for line in diff_section.splitlines() if line.strip()]
    assert diff_lines, "diff section was unexpectedly empty"

    # Banner must appear in the diff section, and it must sit ABOVE the
    # first ``diff --git`` hunk header. That is the real property:
    # the brain sees the mode switch before the content it is interpreting.
    # Position-anchoring on the hunk (not the diagnostic region) keeps the
    # test robust to future changes in how ``_assemble_prompt`` renders
    # ``(diagnostic: ...)`` — which is multi-line because git stderr is.
    fallback_idx = next(
        (i for i, line in enumerate(diff_lines) if line.startswith("(fallback:")),
        None,
    )
    assert fallback_idx is not None, (
        f"banner missing from diff section; first 5 lines: {diff_lines[:5]}"
    )
    hunk_idx = next(
        (i for i, line in enumerate(diff_lines) if line.startswith("diff --git ")),
        len(diff_lines),
    )
    assert fallback_idx < hunk_idx, (
        f"banner must appear before the first diff hunk header; "
        f"fallback_idx={fallback_idx}, hunk_idx={hunk_idx}"
    )
    assert "working-tree" in diff_lines[fallback_idx].lower(), diff_lines[fallback_idx]
