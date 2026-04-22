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

import json
import subprocess  # nosec B404 — list-form read-only gh only; no shell
from pathlib import Path

__all__ = [
    "_GH_TIMEOUT_SECONDS",
    "_PR_VIEW_FIELDS",
    "_fetch_diff",
    "_fetch_pr_metadata",
    "_run_gh",
]

_GH_TIMEOUT_SECONDS = 30.0

# Fields we request from ``gh pr view --json``. Kept as a module-level
# constant so the test pinning the argv contract can reference it.
_PR_VIEW_FIELDS = ",".join(
    [
        "number",
        "title",
        "body",
        "state",
        "mergeable",
        "author",
        "headRefName",
        "baseRefName",
        "url",
        "labels",
        "files",
        "reviewDecision",
        "headRefOid",
        "closingIssuesReferences",
    ]
)


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


def _fetch_pr_metadata(
    pr_number: int,
    cwd: Path,
) -> tuple[dict[str, object], str | None]:
    """Return ``(metadata_dict, error_string_or_None)`` for one PR.

    Invokes ``gh pr view <num> --json <fields>`` and parses the JSON
    output. On non-zero exit or malformed JSON, returns ``({}, error)``
    so the caller can surface the diagnostic in the prompt without
    aborting the whole review (same pattern as
    ``plan_reviewer._capture_diff``).
    """
    try:
        proc = _run_gh(
            ["pr", "view", str(pr_number), "--json", _PR_VIEW_FIELDS],
            cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {}, f"gh pr view {pr_number} failed: {exc}"
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or "non-zero exit"
        return {}, f"gh pr view {pr_number} failed: {stderr}"
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {}, f"gh pr view {pr_number} emitted unparseable JSON: {exc}"
    if not isinstance(parsed, dict):
        return {}, f"gh pr view {pr_number} returned non-object JSON"
    return parsed, None


def _fetch_diff(pr_number: int, cwd: Path) -> tuple[str, str | None]:
    """Return ``(diff_text, error_or_None)`` from ``gh pr diff <num>``.

    The GitHub API's diff endpoint would need manual pagination + merge
    for very large PRs; ``gh pr diff`` handles that for us. Output is
    the raw unified-diff text ready to drop into the assembled prompt.
    """
    try:
        proc = _run_gh(["pr", "diff", str(pr_number)], cwd=cwd)
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"gh pr diff {pr_number} failed: {exc}"
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or "non-zero exit"
        return "", f"gh pr diff {pr_number} failed: {stderr}"
    return proc.stdout, None
