"""``PRReviewer`` — the Phase B PR-digest specialist.

Pattern A (per ``plan_reviewer.py``): Python deterministically pre-loads
PR context via ``gh`` CLI subprocess, assembles a single prompt,
dispatches to the brain once, wraps the reply. The brain's full tool
surface (``mcp__github__*``, Read/Grep/Glob, gated Bash) remains
available for follow-up questions via ``/brain/query`` but does not run
inside this specialist.

Read-only by construction: only ``gh`` subcommands that produce no side
effects are invoked (``pr view``, ``pr diff``, ``pr list``, ``api`` GETs).
The PAT the CLI uses is scoped ``repo:read + pull_requests:read +
issues:read`` — even if an unexpected verb reached ``gh``, GitHub would
403 it. No new ``PreToolUse`` gate at the specialist layer; see
``docs/plans/2026-04-23-pr-reviewer-specialist.md`` for the threat model.
"""

from __future__ import annotations

import subprocess  # nosec B404 — list-form read-only gh only; no shell
from pathlib import Path

__all__ = ["_GH_TIMEOUT_SECONDS", "_run_gh"]

_GH_TIMEOUT_SECONDS = 30.0


def _run_gh(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a read-only ``gh`` subcommand and return the completed process.

    ``check=False`` so the caller can inspect ``returncode`` and surface
    ``stderr`` as diagnostic context in the assembled prompt — an
    individual ``gh`` hiccup should not abort the whole review. List-form
    args per ``.claude/rules/python-standards.md``.
    """
    return subprocess.run(  # noqa: S603  # nosec B603 B607 — list form, read-only gh only; gh resolved via PATH is the intended portable behaviour
        ["gh", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=_GH_TIMEOUT_SECONDS,
    )
