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

# Reuse the module-private plan-discovery helper from plans_mcp. Importing
# by its underscore name across modules is unconventional but here it's
# deliberate: promoting ``_list_plans`` to a public ``list_plans`` would
# require touching every existing test import, and duplicating the logic
# would create a drift surface between specialist and MCP tool.
# TODO(#4): promote `_list_plans` to public `list_plans` before the 2nd
# Phase A specialist lands, so this cross-subpackage private import doesn't
# become precedent.
from reachy_ducky_daemon.brain.plans_mcp import _list_plans

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
    # TODO(#3): add test coverage for the merge-base-failure fallback branch
    # below (feature branch + `main` ref absent). Also prepend a "using
    # working-tree-vs-HEAD fallback" banner to the returned diff text when
    # this path engages so the brain can distinguish fallback from primary.
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
    return fallback.stdout, fallback_err


def _collect_plans(repo: Path) -> list[tuple[str, str]]:
    """Return ``[(relative_path, contents)]`` for every plan file under ``repo``.

    Files whose ``read_text`` raises (non-UTF-8, vanished between the
    ``_list_plans`` walk and the read, permission denied, etc.) are
    skipped silently — the assembled prompt already includes the file's
    path via neighbouring plan files or via the missing-plans
    diagnostic, and bubbling the error up would defeat the "no
    exceptions escape to callers" design constraint.
    """
    # TODO(#5): instead of silently skipping, accumulate (rel, error_string)
    # entries and surface them under an "=== UNREADABLE PLANS ===" prompt
    # header. Current silent skip is asymmetric with `_capture_diff` and
    # `_current_branch` (both surface errors as diagnostics).
    out: list[tuple[str, str]] = []
    for rel in _list_plans(repo):
        try:
            content = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        out.append((rel, content))
    return out


def _assemble_prompt(
    branch: str,
    branch_error: str | None,
    plans: list[tuple[str, str]],
    diff: str,
    diff_error: str | None,
) -> str:
    """Build the final single-string prompt the brain receives.

    The layout is intentionally stable (section headers with underscores
    and uppercase labels) so the brain's attention has fixed landmarks
    between the plan block and the diff block. The directive goes last
    so it sits closest to where the model starts generating.

    TODO(#2): add per-file and total byte caps with a truncation marker.
    Repos with many large plans risk silent context-window overflow at
    the SDK layer; a marked in-prompt truncation is better than opaque
    SDK-level cutoff.
    """
    parts: list[str] = []
    parts.append(f"Branch: {branch}")
    if branch_error is not None:
        parts.append(f"(diagnostic: {branch_error})")
    parts.append("")

    parts.append("=== PLANS ===")
    if not plans:
        parts.append(
            "(no plan or spec files discovered under conventional locations — "
            "docs/plans/**/*.md, specs/**/*.md, root AGENTS.md/CLAUDE.md/SPEC.md, "
            "*.plan.md)",
        )
    else:
        for rel, content in plans:
            parts.append(f"--- {rel} ---")
            parts.append(content.rstrip("\n"))
            parts.append("")
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

    def __init__(self, brain: BrainInterface, repo: Path) -> None:
        """Bind the specialist to a brain and a repo root.

        ``repo`` is expected to be a checked-out git working tree. The
        specialist treats it as a read-only boundary — no command
        issued from here can mutate its state.
        """
        self._brain = brain
        self._repo = repo

    async def review(self) -> SpecialistResponse:
        """Assemble the review prompt, dispatch to the brain, wrap the response.

        Exactly one ``brain.query`` call per invocation.
        """
        branch, branch_error = _current_branch(self._repo)
        plans = _collect_plans(self._repo)
        diff, diff_error = _capture_diff(self._repo, branch)

        prompt = _assemble_prompt(
            branch=branch,
            branch_error=branch_error,
            plans=plans,
            diff=diff,
            diff_error=diff_error,
        )

        response = await self._brain.query(BrainRequest(user_utterance=prompt))
        return SpecialistResponse(name=_SPECIALIST_NAME, summary=response.text)
