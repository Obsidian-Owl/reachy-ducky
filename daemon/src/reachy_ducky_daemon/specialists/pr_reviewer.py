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

from reachy_ducky_protocol.messages import BrainRequest, SpecialistResponse

from reachy_ducky_daemon.brain.interface import BrainInterface

__all__ = [
    "_GH_TIMEOUT_SECONDS",
    "_PR_VIEW_FIELDS",
    "PRReviewer",
    "_assemble_diagnostic_prompt",
    "_assemble_prompt",
    "_current_branch",
    "_derive_flags",
    "_fetch_check_runs",
    "_fetch_diff",
    "_fetch_pr_metadata",
    "_fetch_review_comments",
    "_find_pr_for_branch",
    "_run_gh",
]

_SPECIALIST_NAME = "pr-reviewer"

_DIRECTIVE = (
    "Synthesize a short PR digest for the developer. Be terse and "
    "referentially precise — cite file paths + line numbers, bot names, "
    "CI job names. Cover: (1) does the diff match the PR body / linked "
    "issues, (2) which bot comments are legit vs noisy, (3) CI & "
    "merge-readiness, (4) your risk call (safe to merge / push a fix / "
    "split). Do NOT re-review the diff line by line — Augment and Codex "
    "already did. You are the synthesis layer; stay one level above their "
    "output."
)

_DIAGNOSTIC_DIRECTIVE = (
    "No open PR was found for this branch. Use mcp__github__list_pull_requests "
    "(try state=all), git log / git show, and git status via the gated Bash "
    "tool to investigate. Was there a merged or closed PR for this branch? "
    "Is the branch even pushed to the remote? Are we actually checked out on "
    "the branch the user thinks we are? Return a short, actionable "
    "explanation — not an apology."
)

# GitHub check-run conclusion values that indicate failure. Used by
# ``_derive_flags`` to emit the ``ci-red`` flag. ``cancelled`` and
# ``timed_out`` count as failures for merge-readiness (you can't
# confirm passage); ``neutral`` and ``skipped`` do not.
_FAILURE_CONCLUSIONS = frozenset({"failure", "cancelled", "timed_out", "action_required"})

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


def _fetch_review_comments(
    owner: str,
    repo: str,
    pr_number: int,
    cwd: Path,
) -> tuple[list[dict[str, object]], str | None]:
    """Return ``(comments_list, error_or_None)`` via ``gh api ... --paginate``.

    Line-level review comments are where Augment and Codex bots post
    their critiques. Issue-style PR comments live at a separate endpoint
    and are intentionally not fetched here — the digest focuses on
    structured line-level concerns, not general PR chatter.

    ``--paginate`` stitches multi-page responses transparently so large
    PRs with many review threads don't silently drop trailing comments.
    """
    endpoint = f"/repos/{owner}/{repo}/pulls/{pr_number}/comments"
    try:
        proc = _run_gh(["api", endpoint, "--paginate"], cwd=cwd)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"gh api {endpoint} failed: {exc}"
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or "non-zero exit"
        return [], f"gh api {endpoint} failed: {stderr}"
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return [], f"gh api {endpoint} emitted unparseable JSON: {exc}"
    if not isinstance(parsed, list):
        return [], f"gh api {endpoint} returned non-list JSON (unexpected shape)"
    # Narrow the element type — every list entry should be a JSON object.
    return [c for c in parsed if isinstance(c, dict)], None


def _fetch_check_runs(
    owner: str,
    repo: str,
    head_sha: str,
    cwd: Path,
) -> tuple[list[dict[str, object]], str | None]:
    """Return ``(check_runs_list, error_or_None)`` for the PR's head commit.

    GitHub wraps check-runs in ``{total_count, check_runs: [...]}``;
    unwrap to the list for caller convenience. Status + conclusion per
    run drive the objective ``ci-green`` / ``ci-red`` / ``ci-pending``
    flags the orchestrator emits.
    """
    endpoint = f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs"
    try:
        proc = _run_gh(["api", endpoint], cwd=cwd)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"gh api {endpoint} failed: {exc}"
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or "non-zero exit"
        return [], f"gh api {endpoint} failed: {stderr}"
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return [], f"gh api {endpoint} emitted unparseable JSON: {exc}"
    if not isinstance(parsed, dict) or "check_runs" not in parsed:
        return [], f"gh api {endpoint} returned unexpected shape"
    runs = parsed["check_runs"]
    if not isinstance(runs, list):
        return [], f"gh api {endpoint} returned check_runs of unexpected type"
    return [r for r in runs if isinstance(r, dict)], None


def _current_branch(cwd: Path) -> tuple[str, str | None]:
    """Return ``(branch_name, error_or_None)`` for the checkout at ``cwd``.

    Mirrors ``plan_reviewer._current_branch`` (plan_reviewer.py:89). Kept
    duplicated rather than factored out because the two specialists
    should not import private helpers across modules — if a third
    specialist needs it, promote to a shared ``specialists/_subprocess``
    module at that point.
    """
    try:
        proc = subprocess.run(  # noqa: S603  # nosec B603 B607 — list form, read-only git only
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "unknown", f"git rev-parse failed: {exc}"
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or "non-zero exit"
        return "unknown", f"git rev-parse failed: {stderr}"
    return proc.stdout.strip() or "unknown", None


def _find_pr_for_branch(
    branch: str,
    cwd: Path,
) -> tuple[int | None, str | None]:
    """Return ``(pr_number_or_None, error_or_None)`` for ``branch``'s open PR.

    Uses ``gh pr list --head <branch> --state open --json number`` — the
    repo context is inferred from the ``cwd``'s upstream remote the same
    way ``gh pr view`` infers it.

    **Distinguishing "no PR" from "couldn't ask":** a successful
    ``gh`` call returning an empty list yields ``(None, None)``; a
    subprocess or non-zero exit yields ``(None, error_string)``. The
    orchestrator needs this distinction to decide between "graceful
    no-PR digest" and "real diagnostic".
    """
    try:
        proc = _run_gh(
            ["pr", "list", "--head", branch, "--state", "open", "--json", "number"],
            cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"gh pr list --head {branch} failed: {exc}"
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or "non-zero exit"
        return None, f"gh pr list --head {branch} failed: {stderr}"
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return None, f"gh pr list emitted unparseable JSON: {exc}"
    if not isinstance(parsed, list):
        return None, "gh pr list returned non-list JSON"
    if not parsed:
        return None, None
    first = parsed[0]
    if not isinstance(first, dict) or "number" not in first:
        return None, "gh pr list returned unexpected entry shape"
    number = first["number"]
    if not isinstance(number, int):
        return None, "gh pr list returned non-int PR number"
    return number, None


def _derive_flags(
    pr: dict[str, object],
    comments: list[dict[str, object]],
    check_runs: list[dict[str, object]],
) -> list[str]:
    """Compute objective machine-tags from pre-fetched PR data.

    Deliberately emits *only* facts derivable without an LLM — the risk
    inferences (``risk:scope-creep``, ``recommend:push-fix``) live in
    the brain's summary prose. Flags are stable across brain-output
    evolution and safe for the menu-bar / phase-C interruption tiers to
    branch on directly.

    CI-state precedence mirrors GitHub's own UI: **failure wins over
    pending**. Any ``failure`` / ``cancelled`` / ``timed_out`` /
    ``action_required`` conclusion yields ``ci-red`` even if other runs
    are still in progress; pending-without-failure yields
    ``ci-pending``; all-success yields ``ci-green``. Zero check-runs
    emits no ``ci-*`` flag (absence of signal ≠ green).
    """
    if not pr:
        return ["no-pr-found"]

    flags: list[str] = []

    if check_runs:
        has_failure = any(
            isinstance(r, dict) and r.get("conclusion") in _FAILURE_CONCLUSIONS for r in check_runs
        )
        has_pending = any(
            isinstance(r, dict)
            and (
                r.get("status") == "in_progress"
                or r.get("status") == "queued"
                or (r.get("conclusion") is None and r.get("status") != "completed")
            )
            for r in check_runs
        )
        if has_failure:
            flags.append("ci-red")
        elif has_pending:
            flags.append("ci-pending")
        else:
            flags.append("ci-green")

    if comments:
        flags.append("has-unresolved-comments")

    if pr.get("mergeable") == "CONFLICTING":
        flags.append("merge-conflict")

    return flags


def _assemble_prompt(
    pr: dict[str, object],
    diff: str,
    comments: list[dict[str, object]],
    check_runs: list[dict[str, object]],
    *,
    diff_error: str | None = None,
    comments_error: str | None = None,
    check_runs_error: str | None = None,
) -> str:
    """Build the review prompt.

    Mirrors ``plan_reviewer._assemble_prompt``'s layout: stable section
    headers (``=== FOO ===``) give the brain fixed attention anchors;
    the synthesis directive sits last, closest to where generation
    begins. Comment entries include author login + file:line so a
    follow-up "tell me about that Augment comment" has anchors the
    brain can ``Read`` directly.

    Fetch-error plumbing: when ``gh`` fails on any of the three
    secondary surfaces (diff, comments, check-runs), the orchestrator
    passes the error string through the matching ``*_error`` kwarg.
    The prompt prepends a ``(diagnostic: ...)`` line inside the
    affected section so the brain can distinguish "nothing there" from
    "couldn't see what was there" — silent degradation of fetch errors
    into empty values was a real correctness risk (brain's risk call
    could say "safe to merge, no diff" on a PR whose diff we couldn't
    read).
    """
    parts: list[str] = []
    parts.append(f"PR #{pr.get('number')}: {pr.get('title', '(no title)')}")
    parts.append(f"Branch: {pr.get('headRefName')} → {pr.get('baseRefName')}")
    url = pr.get("url")
    if url:
        parts.append(f"URL: {url}")
    parts.append("")

    parts.append("=== PR BODY ===")
    body = pr.get("body")
    parts.append(str(body) if body else "(empty)")
    parts.append("")

    parts.append("=== DIFF ===")
    if diff_error is not None:
        parts.append(f"(diagnostic: {diff_error})")
    parts.append(diff if diff.strip() else "(empty diff)")
    parts.append("")

    parts.append("=== REVIEW COMMENTS ===")
    if comments_error is not None:
        parts.append(f"(diagnostic: {comments_error})")
    if not comments:
        parts.append("(no line-level review comments)")
    else:
        for c in comments:
            user = c.get("user")
            login = user.get("login", "unknown") if isinstance(user, dict) else "unknown"
            path = c.get("path", "?")
            line = c.get("line", "?")
            body = c.get("body", "")
            parts.append(f"--- {login} on {path}:{line} ---")
            parts.append(str(body))
    parts.append("")

    parts.append("=== CI / CHECK RUNS ===")
    if check_runs_error is not None:
        parts.append(f"(diagnostic: {check_runs_error})")
    if not check_runs:
        parts.append("(no check runs)")
    else:
        for r in check_runs:
            name = r.get("name", "?")
            status = r.get("status", "?")
            conclusion = r.get("conclusion", "?")
            parts.append(f"- {name}: {status} / {conclusion}")
    parts.append("")

    parts.append("=== TASK ===")
    parts.append(_DIRECTIVE)
    return "\n".join(parts)


def _assemble_diagnostic_prompt(
    branch: str,
    branch_error: str | None,
    find_error: str | None,
) -> str:
    """Build the no-PR-found prompt.

    Brain is expected to dig with its tool surface (``mcp__github__*``,
    gated ``git`` Bash, Read/Grep/Glob) and explain *why* there's no PR
    rather than hand back a review of nothing. Mirrors the "error as
    prompt diagnostic" pattern from ``plan_reviewer._capture_diff``.
    """
    parts: list[str] = [f"Branch: {branch}"]
    if branch_error is not None:
        parts.append(f"(branch diagnostic: {branch_error})")
    if find_error is not None:
        parts.append(f"(lookup diagnostic: {find_error})")
    parts.append("")
    parts.append(f"No open PR was found for branch '{branch}'.")
    parts.append("")
    parts.append("=== TASK ===")
    parts.append(_DIAGNOSTIC_DIRECTIVE)
    return "\n".join(parts)


class PRReviewer:
    """Hybrid specialist: pre-load PR surfaces + brain synthesis.

    Matches the :class:`PlanReviewer` shape — deterministic pre-fetch in
    Python, exactly one ``brain.query()`` call per :meth:`review`, wrap
    the reply as :class:`SpecialistResponse`.

    ``owner`` and ``repo_name`` are passed explicitly rather than parsed
    from ``Project.github_repo`` inside the specialist — the caller
    (the server route) already holds the project config and splitting
    the string is its concern, not the specialist's.

    Invocation contract:

    * ``review(pr_number=N)`` → explicit: fetch that PR.
    * ``review(pr_number=None)`` → auto-detect: ``git rev-parse`` →
      ``gh pr list --head <branch>`` → fetch if found.
    * No PR found (for any reason) → diagnostic prompt; brain is
      expected to investigate with its tool surface and return an
      actionable explanation rather than a review-of-nothing.

    Exactly one ``brain.query()`` call fires per :meth:`review` invocation
    regardless of which path is taken.
    """

    def __init__(
        self,
        *,
        brain: BrainInterface,
        repo: Path,
        owner: str,
        repo_name: str,
    ) -> None:
        self._brain = brain
        self._repo = repo
        self._owner = owner
        self._repo_name = repo_name

    async def review(self, *, pr_number: int | None = None) -> SpecialistResponse:
        """Run the full review cycle for the resolved PR (or diagnostic path)."""
        resolved_pr, branch, branch_err, find_err = self._resolve_pr(pr_number)

        if resolved_pr is None:
            return await self._diagnostic_response(
                branch=branch, branch_error=branch_err, find_error=find_err
            )

        pr_meta, meta_err = _fetch_pr_metadata(resolved_pr, cwd=self._repo)
        if not pr_meta:
            # Resolved a number but couldn't fetch — surface the reason.
            return await self._diagnostic_response(
                branch=branch,
                branch_error=None,
                find_error=meta_err or f"could not fetch PR #{resolved_pr}",
            )

        diff, diff_err = _fetch_diff(resolved_pr, cwd=self._repo)
        comments, comments_err = _fetch_review_comments(
            owner=self._owner,
            repo=self._repo_name,
            pr_number=resolved_pr,
            cwd=self._repo,
        )
        head_sha = pr_meta.get("headRefOid")
        check_runs_err: str | None
        if isinstance(head_sha, str) and head_sha:
            check_runs, check_runs_err = _fetch_check_runs(
                owner=self._owner,
                repo=self._repo_name,
                head_sha=head_sha,
                cwd=self._repo,
            )
        else:
            check_runs = []
            # PR metadata fetched successfully but lacks a head SHA — surface
            # as a diagnostic so the brain knows CI silence is "we didn't ask",
            # not "no CI configured".
            check_runs_err = "PR metadata did not include headRefOid — check-runs not fetched"

        prompt = _assemble_prompt(
            pr=pr_meta,
            diff=diff,
            comments=comments,
            check_runs=check_runs,
            diff_error=diff_err,
            comments_error=comments_err,
            check_runs_error=check_runs_err,
        )
        flags = _derive_flags(pr=pr_meta, comments=comments, check_runs=check_runs)
        brain_resp = await self._brain.query(BrainRequest(user_utterance=prompt))
        return SpecialistResponse(name=_SPECIALIST_NAME, summary=brain_resp.text, flags=flags)

    async def _diagnostic_response(
        self,
        *,
        branch: str,
        branch_error: str | None,
        find_error: str | None,
    ) -> SpecialistResponse:
        """Dispatch the diagnostic prompt and wrap the brain's reply."""
        prompt = _assemble_diagnostic_prompt(
            branch=branch or "unknown",
            branch_error=branch_error,
            find_error=find_error,
        )
        flags = _derive_flags(pr={}, comments=[], check_runs=[])
        brain_resp = await self._brain.query(BrainRequest(user_utterance=prompt))
        return SpecialistResponse(name=_SPECIALIST_NAME, summary=brain_resp.text, flags=flags)

    def _resolve_pr(self, pr_number: int | None) -> tuple[int | None, str, str | None, str | None]:
        """Return ``(pr, branch, branch_error, find_error)`` per the resolution order.

        Explicit ``pr_number`` short-circuits the auto-detect path. The
        ``branch`` slot is ``"(explicit)"`` in that case — a sentinel
        the diagnostic path never sees but which keeps the return shape
        uniform.
        """
        if pr_number is not None:
            return pr_number, "(explicit)", None, None

        branch, branch_err = _current_branch(self._repo)
        if branch_err is not None:
            return None, branch, branch_err, None

        pr, find_err = _find_pr_for_branch(branch=branch, cwd=self._repo)
        return pr, branch, None, find_err
