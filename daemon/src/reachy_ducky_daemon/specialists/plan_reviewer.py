"""``PlanReviewer`` — the first Phase A specialist (hybrid Python + brain).

Workflow shape (Pattern A per Anthropic's "Building Effective Agents"):

1. **Python pre-load (deterministic, no LLM).** Enumerate plan/spec files
   under ``repo`` via the conventional-pattern helper reused from
   :mod:`~reachy_ducky_daemon.brain.plans_mcp`, read each file, and
   capture the branch diff vs ``main`` via ``git diff`` subprocess.
2. **Prompt assembly.** Concatenate plan text + diff + a terse
   drift-only directive into a single string.
3. **Brain dispatch.** Await ``brain.query(BrainRequest(...))``. The
   brain decides whether/how to use its tool surface — the specialist
   doesn't care whether it's talking to :class:`MockBrain`, classic
   :class:`ClaudeSDKBrain`, or :meth:`ClaudeSDKBrain.with_tools`.
4. **Wrap.** Return the brain's text as
   :class:`SpecialistResponse(name="plan-reviewer", summary=...)`.

Design choice (option a — direct brain call). The addendum mentions
constructing an ``AgentDefinition`` and dispatching via the brain's
subagent-execution path. In claude-agent-sdk 0.1.64 the ``agents``
field on :class:`ClaudeAgentOptions` is ``dict[str, AgentDefinition] |
None`` — but a subagent definition is only invoked when Claude itself
calls the ``Task`` tool, which is something Python cannot do from
outside the tool-use loop. Options (b) and (c) both require either
bypassing :class:`BrainInterface` or re-implementing streaming —
neither gains much scope restriction over what the brain already
enforces (or doesn't, in classic mode). Keeping the full
``AgentDefinition`` story deferred until a second specialist needs
fresh-context tool restriction.

The specialist is intentionally read-only. Subprocess calls are
``check=False`` with captured stderr that surfaces as diagnostic
context in the prompt — a ``git diff`` that fails shouldn't fail the
whole review; the brain should still produce a useful response. Only
read-only git subcommands (``diff``, ``rev-parse``) are executed.
"""

from __future__ import annotations

import subprocess  # nosec B404 — used only for controlled list-form read-only git calls (not user-shell input)
from pathlib import Path

from reachy_ducky_protocol.messages import BrainRequest, SpecialistResponse

from reachy_ducky_daemon.brain.interface import BrainInterface
from reachy_ducky_daemon.brain.plans_mcp import list_plans
from reachy_ducky_daemon.specialists.redaction import RedactionError, redact

__all__ = ["PlanReviewer"]

_SPECIALIST_NAME = "plan-reviewer"

_DIRECTIVE = (
    "Compare the diff against the plan. Report drift only — where does the "
    "implementation deviate from what the plan specified? Be terse and specific."
)

# Git subcommands the specialist is allowed to run. Read-only by construction:
# ``diff`` and ``rev-parse`` cannot mutate the working tree, index, or refs.
_GIT_TIMEOUT_SECONDS = 30.0


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a read-only ``git`` subcommand and return the completed process.

    ``check=False`` so the caller can inspect ``returncode`` and surface
    stderr as diagnostic context without turning every git hiccup into
    an uncaught exception. List-form args (never ``shell=True``) per
    ``.claude/rules/python-standards.md``.
    """
    return subprocess.run(  # noqa: S603  # nosec B603 B607 — list form, read-only git only; "git" resolved via PATH is the intended portable behaviour
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def _current_branch(repo: Path) -> tuple[str, str | None]:
    """Return ``(branch_name, error)`` for the current checkout.

    ``error`` is :data:`None` when ``git rev-parse`` succeeds. When it
    fails, ``branch_name`` falls back to the literal string ``"unknown"``
    and the stderr surfaces in ``error`` so the assembled prompt can
    include the diagnostic.
    """
    try:
        proc = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    except (OSError, subprocess.SubprocessError) as exc:
        return "unknown", f"git rev-parse failed: {exc}"
    if proc.returncode != 0:
        return "unknown", f"git rev-parse failed: {proc.stderr.strip() or 'non-zero exit'}"
    return proc.stdout.strip() or "unknown", None


def _capture_diff(repo: Path, branch: str) -> tuple[str, str | None]:
    """Return ``(diff_text, error)`` for the drift surface.

    * On a feature branch: ``git diff main...HEAD`` (merge-base diff).
    * On ``main`` (or when the feature-branch diff fails with no output):
      falls back to ``git diff`` (working-tree vs HEAD) so uncommitted
      changes still surface. The spec calls out this fallback explicitly.

    Both paths are ``check=False``; a git failure becomes a diagnostic
    string in the returned ``error`` rather than a raised exception.
    """
    if branch != "main":
        try:
            proc = _run_git(["diff", "main...HEAD"], cwd=repo)
        except (OSError, subprocess.SubprocessError) as exc:
            return "", f"git diff main...HEAD failed: {exc}"
        if proc.returncode == 0:
            return proc.stdout, None
        # Fall through to the uncommitted-vs-HEAD fallback; preserve the
        # original error so the prompt can show why the merge-base diff
        # was skipped.
        fallback_err = f"git diff main...HEAD failed: {proc.stderr.strip() or 'non-zero exit'}"
    else:
        fallback_err = None

    try:
        fallback = _run_git(["diff"], cwd=repo)
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"git diff failed: {exc}"
    if fallback.returncode != 0:
        combined = fallback.stderr.strip() or "non-zero exit"
        if fallback_err is not None:
            return "", f"{fallback_err}; git diff (fallback) failed: {combined}"
        return "", f"git diff failed: {combined}"
    # Prepend a banner when we actually fell back from a merge-base diff
    # (i.e., not the on-main path where fallback_err is None because there
    # was no merge-base attempt in the first place). Lets the brain tell
    # 'working-tree diff because on main' from 'working-tree diff because
    # main ref is absent / merge-base failed'.
    if fallback_err is not None and fallback.stdout:
        banner = (
            "(fallback: using working-tree-vs-HEAD diff; merge-base against main was unavailable)\n"
        )
        return banner + fallback.stdout, fallback_err
    return fallback.stdout, fallback_err


def _truncate_plan_body(body: str, max_chars: int) -> str:
    """Truncate ``body`` to ``max_chars`` with an inline marker if cut.

    Returns ``body`` unchanged when it fits; otherwise returns
    ``body[:max_chars] + "\\n[... truncated: N chars elided ...]\\n"``
    where N is the dropped char count.
    """
    if len(body) <= max_chars:
        return body
    elided = len(body) - max_chars
    return body[:max_chars] + f"\n[... truncated: {elided} chars elided ...]\n"


def _collect_plans(
    repo: Path,
    max_chars_per_plan: int,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return ``(readable, unreadable)``.

    ``readable`` is ``[(rel, content)]`` for every plan that loaded
    cleanly. ``unreadable`` is ``[(rel, error_string)]`` for every plan
    ``list_plans`` advertised but ``read_text`` rejected (non-UTF-8,
    permission-denied, vanished mid-walk, etc.). Both lists are sorted
    by ``rel``; either may be empty. Mirrors the error-surface pattern
    used by ``_current_branch`` and ``_capture_diff`` — the diagnostic
    surfaces to the brain via the ``=== UNREADABLE PLANS ===`` prompt
    section in ``_assemble_prompt`` rather than being silently swallowed.

    ``max_chars_per_plan`` caps each individual readable body via
    :func:`_truncate_plan_body`; oversize bodies are cut with a visible
    inline marker so the brain knows truncation occurred. The cap is
    applied after the successful read, before appending to ``readable``.
    """
    readable: list[tuple[str, str]] = []
    unreadable: list[tuple[str, str]] = []
    for rel in list_plans(repo):
        try:
            content = (repo / rel).read_text(encoding="utf-8")
        except OSError as exc:
            # exc.strerror omits the absolute filename that str(exc) embeds —
            # avoids leaking client-project paths / usernames into the prompt
            # that flows to Claude. The relative path is already in the section
            # header at _assemble_prompt's '--- {rel} ---' line, so the brain
            # has the context it needs. (Code-review I1 follow-up to #5.)
            detail = exc.strerror or str(exc)
            unreadable.append((rel, f"OSError: {detail}"))
            continue
        except UnicodeDecodeError as exc:
            # UnicodeDecodeError.__str__ doesn't embed a path; safe as-is.
            unreadable.append((rel, f"UnicodeDecodeError: {exc}"))
            continue
        readable.append((rel, _truncate_plan_body(content, max_chars_per_plan)))
    return readable, unreadable


def _assemble_plans_block(
    plans: list[tuple[str, str]],
    max_total_chars: int,
) -> list[str]:
    """Build the ``=== PLANS ===`` section under a total-char budget.

    Concatenates plan bodies in order; once the cumulative body length
    would exceed ``max_total_chars`` (after at least one plan has
    landed), appends a single ``[... N plan(s) omitted ...]`` marker
    (``plan`` / ``plans`` grammatically matched to ``N``) and stops.
    The ``(no plans)`` branch is unchanged.

    The "at least one plan included" guard means a single oversized
    plan (longer than the total budget) still lands — so the brain
    always gets at least one plan when any exist. Per-file truncation
    in :func:`_collect_plans` bounds the damage in that edge case.
    """
    parts: list[str] = ["=== PLANS ==="]
    if not plans:
        parts.append(
            "(no plan or spec files discovered under conventional locations — "
            "docs/plans/**/*.md, specs/**/*.md, root AGENTS.md/CLAUDE.md/SPEC.md, "
            "*.plan.md)",
        )
        return parts

    used = 0
    included = 0
    for rel, body in plans:
        body_clean = body.rstrip("\n")
        block = f"--- {rel} ---\n{body_clean}\n"
        # Guarantee at least one plan lands even if it exceeds the budget alone —
        # per-file truncation in _collect_plans bounds that worst case.
        if used + len(block) > max_total_chars and included > 0:
            remaining = len(plans) - included
            plan_word = "plan" if remaining == 1 else "plans"
            parts.append(
                f"[... {remaining} {plan_word} omitted: total body "
                f"budget of {max_total_chars} chars exhausted ...]",
            )
            break
        parts.append(block.rstrip("\n"))
        parts.append("")
        used += len(block)
        included += 1
    return parts


def _assemble_prompt(
    branch: str,
    branch_error: str | None,
    plans: list[tuple[str, str]],
    unreadable_plans: list[tuple[str, str]],
    diff: str,
    diff_error: str | None,
    *,
    max_total_chars: int,
) -> str:
    """Build the final single-string prompt the brain receives.

    The layout is intentionally stable (section headers with underscores
    and uppercase labels) so the brain's attention has fixed landmarks
    between the plan block and the diff block. The directive goes last
    so it sits closest to where the model starts generating.

    When ``unreadable_plans`` is non-empty, a dedicated
    ``=== UNREADABLE PLANS ===`` section sits between ``=== PLANS ===``
    and ``=== DIFF ===`` so the brain can tell "file listed but
    unreadable" from "file never existed". The section is omitted when
    every plan loads cleanly to avoid cluttering the common case.

    ``max_total_chars`` caps the cumulative body length across all
    plans; once exhausted, remaining plans are replaced by a single
    ``[... N plan(s) omitted ...]`` marker (see
    :func:`_assemble_plans_block`). Visible in-prompt truncation is
    better than silent SDK-layer cutoff when Claude's context window
    would be blown by the concatenated plan bodies.
    """
    parts: list[str] = []
    parts.append(f"Branch: {branch}")
    if branch_error is not None:
        parts.append(f"(diagnostic: {branch_error})")
    parts.append("")

    parts.extend(_assemble_plans_block(plans, max_total_chars))
    parts.append("")

    if unreadable_plans:
        parts.append("=== UNREADABLE PLANS ===")
        parts.append(
            "(These files were discovered under conventional locations but "
            "could not be read. Listed here so you can note them in the "
            "review — they do not participate in drift analysis.)",
        )
        for rel, err in unreadable_plans:
            parts.append(f"--- {rel} ---")
            parts.append(f"(diagnostic: {err})")
        parts.append("")

    parts.append("=== DIFF ===")
    if diff_error is not None:
        parts.append(f"(diagnostic: {diff_error})")
    if diff.strip():
        parts.append(diff)
    else:
        parts.append("(no diff output)")
    parts.append("")

    parts.append("=== TASK ===")
    parts.append(_DIRECTIVE)
    return "\n".join(parts)


class PlanReviewer:
    """Hybrid specialist: pre-load plans + diff, then query the brain."""

    def __init__(
        self,
        brain: BrainInterface,
        repo: Path,
        *,
        max_plan_chars: int = 50_000,
        max_total_plan_chars: int = 200_000,
    ) -> None:
        """Bind the specialist to a brain and a repo root.

        ``repo`` is expected to be a checked-out git working tree. The
        specialist treats it as a read-only boundary — no command
        issued from here can mutate its state.

        ``max_plan_chars`` (default 50,000) caps each individual plan's
        body length in the assembled prompt. Bodies over the cap are
        truncated with an inline ``[... truncated: N chars elided ...]``
        marker. Calibrated to typical plan sizes in this repo (the
        Phase A plan is ~3,000 lines / ~140KB); raise via constructor
        for a repo with much larger plans.

        ``max_total_plan_chars`` (default 200,000) caps the cumulative
        body length across all plans. Once the budget is exhausted,
        the remaining plans are replaced by a single
        ``[... N plan(s) omitted ...]`` marker. Calibrated against
        Claude's 200k-token context — chars × ~4-bytes-per-token leaves
        headroom for the diff, the brain's response, and other sections.

        Intended invariant: ``max_plan_chars <= max_total_plan_chars``.
        This is not enforced at construction (individual reviewers may
        deliberately tune one cap above the other), but worst-case
        overshoot of the total budget is bounded at
        ``max_total_plan_chars + max_plan_chars`` because the per-file
        truncation in :func:`_collect_plans` caps any single plan's
        contribution before it reaches the total-budget check in
        :func:`_assemble_plans_block`.

        Both caps are keyword-only so a future signature evolution can
        add positional kwargs without ambiguity. (#2)
        """
        self._brain = brain
        self._repo = repo
        self._max_plan_chars = max_plan_chars
        self._max_total_plan_chars = max_total_plan_chars

    async def review(self) -> SpecialistResponse:
        """Assemble the review prompt, redact secrets, dispatch to the brain.

        Exactly one ``brain.query`` call per invocation — but only when
        redaction succeeds. A :class:`RedactionError` short-circuits to
        a fail-closed diagnostic response; no brain call fires, no
        secret leaks.
        """
        branch, branch_error = _current_branch(self._repo)
        plans, unreadable_plans = _collect_plans(
            self._repo,
            max_chars_per_plan=self._max_plan_chars,
        )
        diff, diff_error = _capture_diff(self._repo, branch)

        prompt = _assemble_prompt(
            branch=branch,
            branch_error=branch_error,
            plans=plans,
            unreadable_plans=unreadable_plans,
            diff=diff,
            diff_error=diff_error,
            max_total_chars=self._max_total_plan_chars,
        )

        try:
            redacted, rule_ids = redact(prompt, cwd=self._repo)
        except RedactionError as exc:
            return SpecialistResponse(
                name=_SPECIALIST_NAME,
                summary=(
                    f"Redaction unavailable: {exc}. Aborting review to "
                    "prevent secret leaks — re-run once the redactor is back."
                ),
                flags=["redaction-failed"],
            )

        response = await self._brain.query(BrainRequest(user_utterance=redacted))
        return SpecialistResponse(
            name=_SPECIALIST_NAME,
            summary=response.text,
            flags=[f"redacted:{rid}" for rid in rule_ids],
        )
