# pr-reviewer Specialist — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Ship the Phase B `pr-reviewer` specialist — a read-only, plan-aware, conversational PR digest. One layer above Augment/Codex/Claude-review: synthesizes intent ↔ diff, rolls up bot reviews, reports CI, calls risk. Not a gate; an advisor.

**Architecture:** Same Pattern A shape as `PlanReviewer`. Python deterministically pre-loads context via `gh` CLI subprocess → assembles a single prompt → one `brain.query()` call → wraps text as `SpecialistResponse`. Follow-up questions route through `/brain/query`, which already has the full `mcp__github__*` + Read/Grep/Glob + gated Bash surface for live deep-dives.

**Invocation model:** `SpecialistRequest.pr_number: int | None`. Resolution order:
1. Explicit `pr_number` → fetch that PR.
2. No `pr_number` → `git rev-parse --abbrev-ref HEAD` in project repo → `gh pr list --head <branch> --state open` → take first.
3. No PR found → diagnostic prompt (brain uses tools to explain why no PR exists) — still returns a `SpecialistResponse`, just a different shape.

**Response shape:** Reuse existing `SpecialistResponse(name, summary, flags)` — no protocol extension. `summary` carries the digest prose. `flags` carries Python-derived objective tags (`ci-green` / `ci-red` / `ci-pending`, `has-unresolved-comments`, `no-pr-found`, `merge-conflict`). Brain inferences stay in `summary` prose.

**Tech Stack:**
- Python 3.12, `uv` workspace
- Pydantic v2 for `SpecialistRequest` extension
- `subprocess` (list-form, `shell=False`) for `gh` CLI
- `gh` CLI as daemon prereq (auth via `GH_TOKEN` = same PAT as `github-mcp-server`)
- `pytest`, `pytest-asyncio`, `unittest.mock.patch` for subprocess mocking
- FastAPI `TestClient` for route tests

**Conventions used throughout:**
- Every task follows TDD: write failing test → run it (fails with expected message) → implement minimal code → run it (passes) → commit.
- Subprocess calls use list-form args (never `shell=True`), `check=False`, `timeout=30.0`, stderr surfaced as diagnostic.
- All test subprocess calls are mocked at `subprocess.run` boundary; no network, no live `gh`.
- Integration tests (`@pytest.mark.integration`) gated on `REACHY_DUCKY_RUN_INTEGRATION=1`.
- Per-task quality gate: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict <touched-packages> && uv run pytest -q` before commit.
- Full-branch quality gate (before pushing): `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict daemon/src app/src menubar/src protocol/src daemon/tests protocol/tests menubar/tests app/tests && uv run pyright && uv run bandit -ll -r daemon/src app/src menubar/src protocol/src && uv run pytest -q --cov`. Coverage must stay ≥ 90%.

**Reference skills:** @superpowers:test-driven-development, @superpowers:verification-before-completion

**Template to mirror:** `daemon/src/reachy_ducky_daemon/specialists/plan_reviewer.py` + `daemon/tests/test_specialist_plan_reviewer.py`. Code patterns, docstring conventions, `_run_*` subprocess helper shape, `_assemble_prompt` structure, and `review()` orchestrator layout all carry over directly.

**Session scope (per handoff 2026-04-22):** Don't try to finish in one session. A good pass: this plan, plus Tasks 1.1 and 1.2 (protocol extension + `_run_gh` scaffolding). Tasks 2.x–6.x carry over to future sessions via the branch + plan doc.

**Deferred:** Secret redaction across specialists → tracked in #50. Not a pr-reviewer blocker; cross-specialist follow-up.

---

## Milestone 1 — Protocol + subprocess scaffolding

### Task 1.1: Extend `SpecialistRequest` with optional `pr_number`

**Files:**
- Modify: `protocol/src/reachy_ducky_protocol/messages.py:44-47`
- Modify: `protocol/tests/test_messages.py`

**Step 1: Write the failing test**

Append to `protocol/tests/test_messages.py`:

```python
def test_specialist_request_pr_number_defaults_to_none() -> None:
    """pr_number is optional on SpecialistRequest — omission leaves it None."""
    req = SpecialistRequest(name="pr-reviewer", project_slug="reachy-ducky")
    assert req.pr_number is None


def test_specialist_request_accepts_pr_number() -> None:
    """pr_number is a plain int when supplied."""
    req = SpecialistRequest(name="pr-reviewer", project_slug="reachy-ducky", pr_number=42)
    assert req.pr_number == 42


def test_specialist_request_rejects_non_int_pr_number() -> None:
    """pr_number must be an int — strings and floats fail validation."""
    with pytest.raises(ValidationError):
        SpecialistRequest.model_validate(
            {"name": "pr-reviewer", "project_slug": "reachy-ducky", "pr_number": "42"}
        )
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest protocol/tests/test_messages.py::test_specialist_request_pr_number_defaults_to_none -v`
Expected: FAIL with `AttributeError: 'SpecialistRequest' object has no attribute 'pr_number'` or a field-not-found Pydantic error.

**Step 3: Write minimal implementation**

Edit `protocol/src/reachy_ducky_protocol/messages.py` at the `SpecialistRequest` class (currently lines 44–47):

```python
class SpecialistRequest(_WireMessage):
    name: str
    project_slug: str
    branch: str | None = None
    pr_number: int | None = None
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest protocol/tests/test_messages.py -v`
Expected: all tests PASS (the three new ones plus the existing ones).

Also run the type-check:
`uv run mypy --strict protocol/src protocol/tests`
Expected: no new errors.

**Step 5: Commit**

```bash
git add protocol/src/reachy_ducky_protocol/messages.py protocol/tests/test_messages.py
git commit -m "feat(protocol): add optional pr_number to SpecialistRequest

Non-breaking additive field — plan-reviewer ignores it; pr-reviewer
(coming next) uses it for explicit PR targeting with branch-attached
auto-detect as the fallback path."
```

---

### Task 1.2: Create `pr_reviewer.py` skeleton with `_run_gh` helper

**Files:**
- Create: `daemon/src/reachy_ducky_daemon/specialists/pr_reviewer.py`
- Create: `daemon/tests/test_specialist_pr_reviewer.py`

**Step 1: Write the failing test**

Create `daemon/tests/test_specialist_pr_reviewer.py`:

```python
"""Tests for :class:`PRReviewer`.

Follows the same shape as test_specialist_plan_reviewer.py — subprocess
calls are mocked at the ``subprocess.run`` boundary (rather than running
real ``gh``, which would need network + auth + a live PR). All canned
``gh`` outputs live under ``daemon/tests/fixtures/gh_*.json``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from reachy_ducky_daemon.specialists.pr_reviewer import _GH_TIMEOUT_SECONDS, _run_gh


def test_run_gh_invokes_list_form_with_timeout() -> None:
    """_run_gh builds a list-form argv and passes the module-wide timeout.

    Never ``shell=True``; list form prevents injection via args. The
    timeout mirrors ``_GIT_TIMEOUT_SECONDS`` in plan_reviewer.py:68.
    """
    fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
    with patch("subprocess.run", return_value=fake_proc) as m:
        result = _run_gh(["pr", "view", "42"], cwd=Path("/tmp"))

    assert result.stdout == "ok"
    m.assert_called_once()
    call_kwargs = m.call_args.kwargs
    assert m.call_args.args[0] == ["gh", "pr", "view", "42"]
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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest daemon/tests/test_specialist_pr_reviewer.py -v`
Expected: FAIL with `ImportError: cannot import name '_run_gh'` (module doesn't exist yet).

**Step 3: Write minimal implementation**

Create `daemon/src/reachy_ducky_daemon/specialists/pr_reviewer.py`:

```python
"""``PRReviewer`` — the Phase B PR-digest specialist.

Pattern A (per plan_reviewer.py): Python deterministically pre-loads PR
context via ``gh`` CLI subprocess, assembles a single prompt, dispatches
to the brain once, wraps the reply. The brain's full tool surface
(``mcp__github__*``, Read/Grep/Glob, gated Bash) remains available for
follow-up questions via ``/brain/query`` but does not run inside this
specialist.

Read-only by construction: only ``gh`` subcommands that produce no side
effects are invoked (``pr view``, ``pr diff``, ``pr list``, ``api GET``).
The PAT the CLI uses is scoped ``repo:read + pull_requests:read +
issues:read`` — even if an unexpected verb reached ``gh``, GitHub would
403 it. No new PreToolUse gate at the specialist layer; see
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
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest daemon/tests/test_specialist_pr_reviewer.py -v`
Expected: both tests PASS.

Run the type-check:
`uv run mypy --strict daemon/src daemon/tests`
Expected: no new errors.

Run bandit to confirm the `nosec` comments are formatted correctly:
`uv run bandit -ll -r daemon/src`
Expected: no medium/high findings.

**Step 5: Commit**

```bash
git add daemon/src/reachy_ducky_daemon/specialists/pr_reviewer.py daemon/tests/test_specialist_pr_reviewer.py
git commit -m "feat(specialists): add pr_reviewer module with _run_gh helper

Mirrors plan_reviewer's _run_git shape: list-form args, check=False,
stderr-as-diagnostic, 30s timeout. Threat model: read-only PAT scoping
+ hardcoded verbs + no LLM in the loop = no new PreToolUse gate needed
at the specialist layer."
```

---

## Milestone 2 — GitHub data fetch helpers

### Task 2.1: PR metadata fetch (`_fetch_pr_metadata`)

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/specialists/pr_reviewer.py`
- Modify: `daemon/tests/test_specialist_pr_reviewer.py`
- Create: `daemon/tests/fixtures/gh_pr_view_happy.json`

**Step 1: Write the failing test**

Add to `daemon/tests/test_specialist_pr_reviewer.py`:

```python
def test_fetch_pr_metadata_parses_gh_pr_view_json(tmp_path: Path) -> None:
    """_fetch_pr_metadata invokes `gh pr view --json ...` and returns parsed dict."""
    fixture = Path(__file__).parent / "fixtures" / "gh_pr_view_happy.json"
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
    # List form + --json fields.
    argv = m.call_args.args[0]
    assert argv[:3] == ["gh", "pr", "view"]
    assert "42" in argv
    assert "--json" in argv


def test_fetch_pr_metadata_surfaces_gh_failure_as_diagnostic(tmp_path: Path) -> None:
    """Non-zero gh exit returns (empty_dict, error_string) — no exception."""
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="GraphQL: Could not resolve to a PullRequest"
    )
    with patch("subprocess.run", return_value=fake_proc):
        meta, err = _fetch_pr_metadata(pr_number=999999, cwd=tmp_path)

    assert meta == {}
    assert err is not None
    assert "999999" in err or "Could not resolve" in err
```

Create `daemon/tests/fixtures/gh_pr_view_happy.json`:

```json
{
  "number": 42,
  "title": "feat: add retry logic",
  "body": "Closes #15. Adds exponential backoff to the upload path.",
  "state": "OPEN",
  "mergeable": "MERGEABLE",
  "author": {"login": "macattak"},
  "headRefName": "feat-retry",
  "baseRefName": "main",
  "url": "https://github.com/Obsidian-Owl/reachy-ducky/pull/42",
  "labels": [],
  "files": [{"path": "src/upload.py", "additions": 20, "deletions": 3}],
  "reviewDecision": "CHANGES_REQUESTED",
  "headRefOid": "abc123def456",
  "closingIssuesReferences": [{"number": 15, "title": "Upload flake on slow connections"}]
}
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest daemon/tests/test_specialist_pr_reviewer.py::test_fetch_pr_metadata_parses_gh_pr_view_json -v`
Expected: FAIL with `ImportError: cannot import name '_fetch_pr_metadata'`.

**Step 3: Write minimal implementation**

Append to `daemon/src/reachy_ducky_daemon/specialists/pr_reviewer.py`:

```python
import json

_PR_VIEW_FIELDS = ",".join([
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
])


def _fetch_pr_metadata(
    pr_number: int,
    cwd: Path,
) -> tuple[dict[str, object], str | None]:
    """Return ``(metadata_dict, error_string_or_None)`` for one PR.

    Invokes ``gh pr view <num> --json <fields>`` and parses the JSON
    output. On non-zero exit or malformed JSON, returns ``({}, error)``
    so the caller can surface the diagnostic in the prompt.
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
```

Add `_fetch_pr_metadata` and `_PR_VIEW_FIELDS` to `__all__`.

**Step 4: Run tests**

`uv run pytest daemon/tests/test_specialist_pr_reviewer.py -v`
Expected: all PASS.

`uv run mypy --strict daemon/src daemon/tests`
Expected: clean.

**Step 5: Commit**

```bash
git add daemon/src/reachy_ducky_daemon/specialists/pr_reviewer.py daemon/tests/test_specialist_pr_reviewer.py daemon/tests/fixtures/gh_pr_view_happy.json
git commit -m "feat(specialists/pr-reviewer): fetch PR metadata via gh pr view --json

One call covers title, body, state, mergeable, head/base branches, files,
linked issues, CI decision, and head SHA. Non-zero exit surfaces as a
diagnostic string (mirrors plan_reviewer's error-as-data pattern)."
```

---

### Task 2.2: Diff fetch (`_fetch_diff`)

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/specialists/pr_reviewer.py`
- Modify: `daemon/tests/test_specialist_pr_reviewer.py`

**Step 1: Failing test**

```python
def test_fetch_diff_invokes_gh_pr_diff(tmp_path: Path) -> None:
    """_fetch_diff runs `gh pr diff <num>` and returns (stdout, None) on success."""
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="diff --git a/src/x.py b/src/x.py\n+x = 2\n", stderr=""
    )
    with patch("subprocess.run", return_value=fake_proc) as m:
        diff, err = _fetch_diff(pr_number=42, cwd=tmp_path)

    assert err is None
    assert "+x = 2" in diff
    assert m.call_args.args[0] == ["gh", "pr", "diff", "42"]


def test_fetch_diff_surfaces_failure_as_diagnostic(tmp_path: Path) -> None:
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="no such PR"
    )
    with patch("subprocess.run", return_value=fake_proc):
        diff, err = _fetch_diff(pr_number=42, cwd=tmp_path)

    assert diff == ""
    assert err is not None and "no such PR" in err
```

**Step 2:** Run test — expect `ImportError`.

**Step 3:** Implement:

```python
def _fetch_diff(pr_number: int, cwd: Path) -> tuple[str, str | None]:
    """Return ``(diff_text, error_or_None)`` from ``gh pr diff``."""
    try:
        proc = _run_gh(["pr", "diff", str(pr_number)], cwd=cwd)
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"gh pr diff {pr_number} failed: {exc}"
    if proc.returncode != 0:
        return "", f"gh pr diff {pr_number} failed: {proc.stderr.strip() or 'non-zero exit'}"
    return proc.stdout, None
```

**Step 4:** Run tests — expect PASS. Run mypy — expect clean.

**Step 5:** Commit:

```bash
git commit -m "feat(specialists/pr-reviewer): fetch raw diff via gh pr diff"
```

---

### Task 2.3: Review comments fetch (`_fetch_review_comments`)

**Files:** same two modules + `daemon/tests/fixtures/gh_api_comments.json`.

**Step 1: Failing test**

```python
def test_fetch_review_comments_calls_gh_api(tmp_path: Path) -> None:
    """_fetch_review_comments hits /repos/{owner}/{repo}/pulls/{num}/comments via gh api."""
    fixture = Path(__file__).parent / "fixtures" / "gh_api_comments.json"
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=fixture.read_text(), stderr=""
    )
    with patch("subprocess.run", return_value=fake_proc) as m:
        comments, err = _fetch_review_comments(
            owner="Obsidian-Owl", repo="reachy-ducky", pr_number=42, cwd=tmp_path
        )

    assert err is None
    assert len(comments) == 2
    assert comments[0]["user"]["login"] == "augment-code[bot]"
    argv = m.call_args.args[0]
    assert argv[:3] == ["gh", "api"]
    assert "/repos/Obsidian-Owl/reachy-ducky/pulls/42/comments" in argv


def test_fetch_review_comments_empty_on_failure(tmp_path: Path) -> None:
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="404 Not Found"
    )
    with patch("subprocess.run", return_value=fake_proc):
        comments, err = _fetch_review_comments(
            owner="o", repo="r", pr_number=1, cwd=tmp_path
        )

    assert comments == []
    assert err is not None
```

Create `daemon/tests/fixtures/gh_api_comments.json`:

```json
[
  {
    "id": 1,
    "user": {"login": "augment-code[bot]"},
    "path": "src/upload.py",
    "line": 42,
    "body": "This retry loop lacks a jitter term — will synchronize on multi-client failures."
  },
  {
    "id": 2,
    "user": {"login": "codex[bot]"},
    "path": "src/auth.py",
    "line": 7,
    "body": "Potential timing-attack surface on the comparison."
  }
]
```

**Step 2:** Run test — expect `ImportError`.

**Step 3:** Implement:

```python
def _fetch_review_comments(
    owner: str,
    repo: str,
    pr_number: int,
    cwd: Path,
) -> tuple[list[dict[str, object]], str | None]:
    """Return ``(comments_list, error_or_None)`` from gh api.

    Comments are line-level review comments — the surface Augment and
    Codex bots post into. Issue-style PR comments live at a different
    endpoint and are intentionally not fetched here (digest focuses on
    line-level critique, not general PR chatter).
    """
    endpoint = f"/repos/{owner}/{repo}/pulls/{pr_number}/comments"
    try:
        proc = _run_gh(["api", endpoint, "--paginate"], cwd=cwd)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"gh api {endpoint} failed: {exc}"
    if proc.returncode != 0:
        return [], f"gh api {endpoint} failed: {proc.stderr.strip() or 'non-zero exit'}"
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return [], f"gh api {endpoint} emitted unparseable JSON: {exc}"
    if not isinstance(parsed, list):
        return [], f"gh api {endpoint} returned non-list JSON"
    return parsed, None
```

**Step 4:** Run tests — expect PASS.

**Step 5:** Commit:

```bash
git commit -m "feat(specialists/pr-reviewer): fetch line-level review comments via gh api"
```

---

### Task 2.4: CI check-runs fetch (`_fetch_check_runs`)

**Files:** same two modules + `daemon/tests/fixtures/gh_api_check_runs.json`.

**Step 1: Failing test**

```python
def test_fetch_check_runs_calls_gh_api_at_head_sha(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "gh_api_check_runs.json"
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=fixture.read_text(), stderr=""
    )
    with patch("subprocess.run", return_value=fake_proc) as m:
        runs, err = _fetch_check_runs(
            owner="Obsidian-Owl", repo="reachy-ducky", head_sha="abc123", cwd=tmp_path
        )

    assert err is None
    assert len(runs) == 3
    argv = m.call_args.args[0]
    assert argv[:3] == ["gh", "api"]
    assert "/repos/Obsidian-Owl/reachy-ducky/commits/abc123/check-runs" in argv


def test_fetch_check_runs_unwraps_github_envelope(tmp_path: Path) -> None:
    """GitHub returns {total_count, check_runs: [...]} — function returns the list."""
    envelope = '{"total_count": 1, "check_runs": [{"name": "mypy", "conclusion": "success"}]}'
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=envelope, stderr=""
    )
    with patch("subprocess.run", return_value=fake_proc):
        runs, err = _fetch_check_runs(
            owner="o", repo="r", head_sha="sha", cwd=tmp_path
        )

    assert err is None
    assert runs == [{"name": "mypy", "conclusion": "success"}]
```

Create `daemon/tests/fixtures/gh_api_check_runs.json`:

```json
{
  "total_count": 3,
  "check_runs": [
    {"name": "ruff", "status": "completed", "conclusion": "success"},
    {"name": "mypy", "status": "completed", "conclusion": "failure"},
    {"name": "pytest", "status": "in_progress", "conclusion": null}
  ]
}
```

**Step 2:** Run test — expect `ImportError`.

**Step 3:** Implement:

```python
def _fetch_check_runs(
    owner: str,
    repo: str,
    head_sha: str,
    cwd: Path,
) -> tuple[list[dict[str, object]], str | None]:
    """Return ``(check_runs_list, error_or_None)`` for the PR's head commit.

    GitHub wraps check-runs in ``{total_count, check_runs: [...]}``; we
    unwrap to the list for caller convenience.
    """
    endpoint = f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs"
    try:
        proc = _run_gh(["api", endpoint], cwd=cwd)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"gh api {endpoint} failed: {exc}"
    if proc.returncode != 0:
        return [], f"gh api {endpoint} failed: {proc.stderr.strip() or 'non-zero exit'}"
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return [], f"gh api {endpoint} emitted unparseable JSON: {exc}"
    if not isinstance(parsed, dict) or "check_runs" not in parsed:
        return [], f"gh api {endpoint} returned unexpected shape"
    runs = parsed["check_runs"]
    if not isinstance(runs, list):
        return [], f"gh api {endpoint} returned check_runs of wrong type"
    return runs, None
```

**Step 4–5:** Run tests, commit:

```bash
git commit -m "feat(specialists/pr-reviewer): fetch CI check-runs for PR head SHA"
```

---

### Task 2.5: Auto-detect — current branch + find-PR-for-branch

**Files:** same two modules.

**Step 1: Failing tests**

```python
def test_current_branch_reads_rev_parse(tmp_path: Path) -> None:
    """_current_branch shells `git rev-parse --abbrev-ref HEAD`."""
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="feat-retry\n", stderr=""
    )
    with patch("subprocess.run", return_value=fake_proc) as m:
        branch, err = _current_branch(tmp_path)

    assert branch == "feat-retry"
    assert err is None
    assert m.call_args.args[0] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]


def test_find_pr_for_branch_picks_first_open(tmp_path: Path) -> None:
    """_find_pr_for_branch returns the first open PR number for the given branch."""
    stdout = '[{"number": 42, "state": "OPEN"}]'
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=stdout, stderr=""
    )
    with patch("subprocess.run", return_value=fake_proc) as m:
        pr_number, err = _find_pr_for_branch(branch="feat-retry", cwd=tmp_path)

    assert pr_number == 42
    assert err is None
    argv = m.call_args.args[0]
    assert argv[:3] == ["gh", "pr", "list"]
    assert "--head" in argv
    assert "feat-retry" in argv


def test_find_pr_for_branch_none_when_no_pr(tmp_path: Path) -> None:
    """Empty gh output → (None, None) — not an error, just no PR attached."""
    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="[]", stderr=""
    )
    with patch("subprocess.run", return_value=fake_proc):
        pr_number, err = _find_pr_for_branch(branch="feat-retry", cwd=tmp_path)

    assert pr_number is None
    assert err is None
```

**Step 2:** Run tests — expect `ImportError` for the two new symbols.

**Step 3: Implementation**

```python
def _current_branch(cwd: Path) -> tuple[str, str | None]:
    """Return ``(branch_name, error_or_None)`` for the checkout at ``cwd``.

    Mirrors plan_reviewer._current_branch (plan_reviewer.py:89). Kept
    duplicated rather than factored out because the two specialists
    should not import private helpers from each other — if a third
    specialist needs it, promote to ``specialists/_subprocess.py``.
    """
    try:
        proc = subprocess.run(  # noqa: S603  # nosec B603 B607
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
        return "unknown", f"git rev-parse failed: {proc.stderr.strip() or 'non-zero exit'}"
    return proc.stdout.strip() or "unknown", None


def _find_pr_for_branch(
    branch: str,
    cwd: Path,
) -> tuple[int | None, str | None]:
    """Return ``(pr_number_or_None, error_or_None)`` for the branch's open PR.

    Uses ``gh pr list --head <branch> --state open --json number`` — so
    the repo context is inferred from ``cwd``'s upstream remote (the
    same way ``gh pr view`` infers it). Picks the first result if
    multiple open PRs share the head ref.
    """
    try:
        proc = _run_gh(
            ["pr", "list", "--head", branch, "--state", "open", "--json", "number"],
            cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"gh pr list --head {branch} failed: {exc}"
    if proc.returncode != 0:
        return None, f"gh pr list --head {branch} failed: {proc.stderr.strip() or 'non-zero exit'}"
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return None, f"gh pr list emitted unparseable JSON: {exc}"
    if not isinstance(parsed, list) or not parsed:
        return None, None
    first = parsed[0]
    if not isinstance(first, dict) or "number" not in first:
        return None, "gh pr list returned unexpected shape"
    number = first["number"]
    if not isinstance(number, int):
        return None, "gh pr list returned non-int PR number"
    return number, None
```

**Step 4–5:** Run tests, commit:

```bash
git commit -m "feat(specialists/pr-reviewer): auto-detect PR from current branch

Adds _current_branch + _find_pr_for_branch. Resolution order in the
orchestrator: explicit pr_number > auto-detect from HEAD > graceful
diagnostic prompt (next task)."
```

---

## Milestone 3 — Flag derivation + prompt assembly

### Task 3.1: Flag derivation (`_derive_flags`)

**Files:** same two modules.

**Step 1: Failing tests**

```python
def test_derive_flags_emits_ci_status_from_check_runs() -> None:
    """ci-green when all check-runs succeed; ci-red if any fails; ci-pending if any in_progress."""
    all_green = [{"conclusion": "success"}, {"conclusion": "success"}]
    assert "ci-green" in _derive_flags(pr={}, comments=[], check_runs=all_green)

    has_red = [{"conclusion": "success"}, {"conclusion": "failure"}]
    assert "ci-red" in _derive_flags(pr={}, comments=[], check_runs=has_red)

    has_pending = [{"conclusion": "success"}, {"status": "in_progress", "conclusion": None}]
    assert "ci-pending" in _derive_flags(pr={}, comments=[], check_runs=has_pending)


def test_derive_flags_emits_has_unresolved_comments_when_nonzero() -> None:
    assert "has-unresolved-comments" in _derive_flags(
        pr={}, comments=[{"id": 1}], check_runs=[]
    )
    assert "has-unresolved-comments" not in _derive_flags(
        pr={}, comments=[], check_runs=[]
    )


def test_derive_flags_emits_merge_conflict_from_mergeable_state() -> None:
    assert "merge-conflict" in _derive_flags(
        pr={"mergeable": "CONFLICTING"}, comments=[], check_runs=[]
    )
    assert "merge-conflict" not in _derive_flags(
        pr={"mergeable": "MERGEABLE"}, comments=[], check_runs=[]
    )


def test_derive_flags_emits_no_pr_found_when_pr_dict_empty() -> None:
    assert "no-pr-found" in _derive_flags(pr={}, comments=[], check_runs=[])
    assert "no-pr-found" not in _derive_flags(
        pr={"number": 42}, comments=[], check_runs=[]
    )
```

**Step 2:** Run tests — `ImportError`.

**Step 3: Implementation**

```python
def _derive_flags(
    pr: dict[str, object],
    comments: list[dict[str, object]],
    check_runs: list[dict[str, object]],
) -> list[str]:
    """Compute objective machine-tags from pre-fetched data.

    Deliberately *does not* emit risk-inference flags (``risk:scope-creep``,
    ``recommend:push-fix``) — those live in the brain's summary prose.
    Flags here are derivable without an LLM, stay stable across brain
    output evolution, and are safe for the menu-bar app to branch on.
    """
    flags: list[str] = []

    if not pr:
        flags.append("no-pr-found")
        return flags

    if any(
        (isinstance(r, dict) and r.get("status") == "in_progress")
        or (isinstance(r, dict) and r.get("conclusion") is None and r.get("status") != "completed")
        for r in check_runs
    ):
        flags.append("ci-pending")
    elif any(
        isinstance(r, dict) and r.get("conclusion") in {"failure", "cancelled", "timed_out"}
        for r in check_runs
    ):
        flags.append("ci-red")
    elif check_runs:
        flags.append("ci-green")

    if comments:
        flags.append("has-unresolved-comments")

    if pr.get("mergeable") == "CONFLICTING":
        flags.append("merge-conflict")

    return flags
```

**Step 4–5:** Run tests, commit:

```bash
git commit -m "feat(specialists/pr-reviewer): derive objective flags from prefetched data

Flags: ci-green/red/pending, has-unresolved-comments, merge-conflict,
no-pr-found. Risk inferences (scope-creep, recommend-*) stay in summary
prose — flags are for deterministic downstream consumers (menu bar,
phase-C interruption tiers)."
```

---

### Task 3.2: Prompt assembly (happy + diagnostic paths)

**Files:** same two modules.

**Step 1: Failing tests**

```python
def test_assemble_prompt_includes_title_body_diff_comments_ci() -> None:
    prompt = _assemble_prompt(
        pr={
            "number": 42,
            "title": "feat: add retry logic",
            "body": "Closes #15.",
            "headRefName": "feat-retry",
            "baseRefName": "main",
            "url": "https://github.com/Obsidian-Owl/reachy-ducky/pull/42",
        },
        diff="diff --git a/src/x.py\n+x = 2\n",
        comments=[
            {
                "user": {"login": "augment-code[bot]"},
                "path": "src/x.py",
                "line": 2,
                "body": "no jitter",
            }
        ],
        check_runs=[{"name": "mypy", "conclusion": "success"}],
        plan_context="=== PLAN ===\nPlan says Y\n",
    )
    assert "feat: add retry logic" in prompt
    assert "Closes #15" in prompt
    assert "+x = 2" in prompt
    assert "augment-code[bot]" in prompt
    assert "no jitter" in prompt
    assert "mypy" in prompt
    assert "Plan says Y" in prompt
    # Directive is the contract — substring anchor only.
    assert "Synthesize" in prompt or "digest" in prompt.lower()


def test_assemble_diagnostic_prompt_explains_no_pr() -> None:
    prompt = _assemble_diagnostic_prompt(
        branch="feat-retry",
        branch_error=None,
        find_error=None,
    )
    assert "feat-retry" in prompt
    assert "no open PR" in prompt.lower() or "no PR" in prompt.lower()
    # Directive asks the brain to investigate with tools.
    assert "investigate" in prompt.lower() or "check" in prompt.lower()
```

**Step 2:** Run — `ImportError`.

**Step 3: Implementation**

```python
_DIRECTIVE = (
    "Synthesize a short PR digest for the developer. Be terse and referentially "
    "precise (file paths + line numbers, bot names, CI job names). Cover: "
    "(1) does the diff match the PR body / linked issues, (2) which bot "
    "comments are legit vs noisy, (3) CI & merge-readiness, (4) your risk call "
    "(safe to merge / push a fix / split). Do not re-review the diff line by "
    "line — Augment and Codex already did. You are the synthesis layer."
)

_DIAGNOSTIC_DIRECTIVE = (
    "No open PR was found for this branch. Use mcp__github__list_pull_requests "
    "(including state=all), git log / git show, and git status to investigate. "
    "Was there a merged or closed PR? Is the branch even pushed to the remote? "
    "Are we actually checked out on the branch the user thinks? Return a short, "
    "actionable explanation — not an apology."
)


def _assemble_prompt(
    pr: dict[str, object],
    diff: str,
    comments: list[dict[str, object]],
    check_runs: list[dict[str, object]],
    plan_context: str = "",
) -> str:
    """Build the review prompt. Mirrors plan_reviewer._assemble_prompt shape."""
    parts: list[str] = []
    parts.append(f"PR #{pr.get('number')}: {pr.get('title', '(no title)')}")
    parts.append(f"Branch: {pr.get('headRefName')} → {pr.get('baseRefName')}")
    url = pr.get("url")
    if url:
        parts.append(f"URL: {url}")
    parts.append("")

    parts.append("=== PR BODY ===")
    parts.append(str(pr.get("body") or "(empty)"))
    parts.append("")

    if plan_context:
        parts.append(plan_context)
        parts.append("")

    parts.append("=== DIFF ===")
    parts.append(diff if diff.strip() else "(empty diff)")
    parts.append("")

    parts.append("=== REVIEW COMMENTS ===")
    if not comments:
        parts.append("(no line-level review comments)")
    else:
        for c in comments:
            user = c.get("user", {})
            login = user.get("login", "unknown") if isinstance(user, dict) else "unknown"
            path = c.get("path", "?")
            line = c.get("line", "?")
            body = c.get("body", "")
            parts.append(f"--- {login} on {path}:{line} ---")
            parts.append(str(body))
    parts.append("")

    parts.append("=== CI / CHECK RUNS ===")
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
    """Build a diagnostic-shaped prompt when no PR was resolvable.

    Brain is expected to dig with its tool surface and explain *why*
    there's no PR rather than produce a review of nothing.
    """
    parts = [f"Branch: {branch}"]
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
```

**Step 4–5:** Run tests, commit:

```bash
git commit -m "feat(specialists/pr-reviewer): assemble happy-path + diagnostic prompts

Happy path: PR metadata + PR body + diff + review comments + CI + optional
plan context + synthesis directive.
Diagnostic path (no-PR-found): branch + diagnostics + tool-investigation
directive. Mirrors plan_reviewer._assemble_prompt shape with stable section
headers for attention anchors."
```

---

## Milestone 4 — Orchestrator

### Task 4.1: `PRReviewer` class with `review(pr_number=None)`

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/specialists/pr_reviewer.py`
- Modify: `daemon/tests/test_specialist_pr_reviewer.py`

**Step 1: Failing tests**

End-to-end tests with the full subprocess.run surface mocked and a `MockBrain`. Exercise all three paths:

```python
import pytest
from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_daemon.specialists.pr_reviewer import PRReviewer
from reachy_ducky_protocol.messages import SpecialistResponse


def _mock_gh_and_git(returns: dict[tuple[str, ...], subprocess.CompletedProcess[str]]):
    """Build a side_effect that dispatches to canned CompletedProcess values by argv."""
    def _side_effect(argv, *args, **kwargs):
        key = tuple(argv)
        for pat, proc in returns.items():
            if key[: len(pat)] == pat:
                return proc
        raise AssertionError(f"unexpected argv: {argv}")
    return _side_effect


@pytest.mark.asyncio
async def test_review_explicit_pr_number_happy_path(tmp_path: Path) -> None:
    pr_view = Path(__file__).parent / "fixtures" / "gh_pr_view_happy.json"
    comments = Path(__file__).parent / "fixtures" / "gh_api_comments.json"
    check_runs = Path(__file__).parent / "fixtures" / "gh_api_check_runs.json"
    ok = lambda stdout: subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")

    side_effect = _mock_gh_and_git({
        ("gh", "pr", "view"): ok(pr_view.read_text()),
        ("gh", "pr", "diff"): ok("diff --git a/x.py\n+x = 2\n"),
        ("gh", "api", "/repos/Obsidian-Owl/reachy-ducky/pulls/42/comments"): ok(comments.read_text()),
        ("gh", "api", "/repos/Obsidian-Owl/reachy-ducky/commits/abc123def456/check-runs"): ok(check_runs.read_text()),
    })

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
    assert len(brain.calls) == 1
    prompt = brain.calls[0].user_utterance
    assert "feat: add retry logic" in prompt
    assert "+x = 2" in prompt
    assert "augment-code[bot]" in prompt
    # Objective flags flow through deterministically.
    assert "ci-red" in response.flags  # mypy failure in fixture
    assert "has-unresolved-comments" in response.flags


@pytest.mark.asyncio
async def test_review_auto_detect_from_current_branch(tmp_path: Path) -> None:
    ok = lambda stdout: subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
    pr_view = Path(__file__).parent / "fixtures" / "gh_pr_view_happy.json"

    side_effect = _mock_gh_and_git({
        ("git", "rev-parse"): ok("feat-retry\n"),
        ("gh", "pr", "list"): ok('[{"number": 42}]'),
        ("gh", "pr", "view"): ok(pr_view.read_text()),
        ("gh", "pr", "diff"): ok(""),
        ("gh", "api"): ok("[]"),  # both comments + check_runs
    })

    brain = MockBrain()
    reviewer = PRReviewer(
        brain=brain, repo=tmp_path, owner="Obsidian-Owl", repo_name="reachy-ducky"
    )
    with patch("subprocess.run", side_effect=side_effect):
        response = await reviewer.review(pr_number=None)

    assert response.name == "pr-reviewer"
    assert len(brain.calls) == 1
    assert "feat: add retry logic" in brain.calls[0].user_utterance


@pytest.mark.asyncio
async def test_review_graceful_fail_when_no_pr_for_branch(tmp_path: Path) -> None:
    ok = lambda stdout: subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")

    side_effect = _mock_gh_and_git({
        ("git", "rev-parse"): ok("feat-orphan\n"),
        ("gh", "pr", "list"): ok("[]"),
    })

    brain = MockBrain()
    reviewer = PRReviewer(
        brain=brain, repo=tmp_path, owner="Obsidian-Owl", repo_name="reachy-ducky"
    )
    with patch("subprocess.run", side_effect=side_effect):
        response = await reviewer.review(pr_number=None)

    assert response.name == "pr-reviewer"
    assert "no-pr-found" in response.flags
    assert len(brain.calls) == 1
    prompt = brain.calls[0].user_utterance
    assert "feat-orphan" in prompt
    assert "no open pr" in prompt.lower() or "no pr" in prompt.lower()
```

**Step 2:** Run — `ImportError: cannot import name 'PRReviewer'`.

**Step 3: Implementation**

```python
from reachy_ducky_protocol.messages import BrainRequest, SpecialistResponse

from reachy_ducky_daemon.brain.interface import BrainInterface

_SPECIALIST_NAME = "pr-reviewer"


class PRReviewer:
    """Hybrid specialist: pre-load PR surfaces + brain digest.

    Matches the ``PlanReviewer`` shape — deterministic pre-fetch in
    Python, one ``brain.query()`` call, wrap the reply. Owner/repo are
    passed explicitly (the caller already holds per-project
    ``github_repo`` config); parsing ``<owner>/<repo>`` from the wire
    would duplicate that concern.
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
        resolved_pr, branch, branch_err, find_err = self._resolve_pr(pr_number)

        if resolved_pr is None:
            prompt = _assemble_diagnostic_prompt(
                branch=branch, branch_error=branch_err, find_error=find_err
            )
            flags = _derive_flags(pr={}, comments=[], check_runs=[])
            response = await self._brain.query(BrainRequest(user_utterance=prompt))
            return SpecialistResponse(
                name=_SPECIALIST_NAME, summary=response.text, flags=flags
            )

        pr_meta, _meta_err = _fetch_pr_metadata(resolved_pr, cwd=self._repo)
        diff, _diff_err = _fetch_diff(resolved_pr, cwd=self._repo)
        comments, _c_err = _fetch_review_comments(
            owner=self._owner, repo=self._repo_name,
            pr_number=resolved_pr, cwd=self._repo,
        )
        head_sha = pr_meta.get("headRefOid")
        if isinstance(head_sha, str):
            check_runs, _ci_err = _fetch_check_runs(
                owner=self._owner, repo=self._repo_name,
                head_sha=head_sha, cwd=self._repo,
            )
        else:
            check_runs = []

        prompt = _assemble_prompt(
            pr=pr_meta, diff=diff, comments=comments, check_runs=check_runs
        )
        flags = _derive_flags(pr=pr_meta, comments=comments, check_runs=check_runs)
        response = await self._brain.query(BrainRequest(user_utterance=prompt))
        return SpecialistResponse(
            name=_SPECIALIST_NAME, summary=response.text, flags=flags
        )

    def _resolve_pr(
        self,
        pr_number: int | None,
    ) -> tuple[int | None, str, str | None, str | None]:
        """Return ``(pr, branch, branch_err, find_err)``.

        Explicit ``pr_number`` short-circuits the lookup. Otherwise:
        read HEAD branch, then ``gh pr list --head``. Missing PR is a
        graceful ``None`` — not an error.
        """
        if pr_number is not None:
            return pr_number, "(explicit)", None, None

        branch, branch_err = _current_branch(self._repo)
        if branch_err is not None:
            return None, branch, branch_err, None

        pr, find_err = _find_pr_for_branch(branch=branch, cwd=self._repo)
        return pr, branch, None, find_err
```

Export `PRReviewer` in `__all__`.

**Step 4–5:** Run tests, commit:

```bash
git commit -m "feat(specialists): PRReviewer orchestrator with three-path resolution

Paths: explicit pr_number > auto-detect from current branch > diagnostic
prompt when no open PR found. One brain.query() per call regardless of
path. Matches PlanReviewer's shape and invocation contract."
```

---

## Milestone 5 — Server + client wiring

### Task 5.1: `POST /specialists/pr-reviewer` route

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/server.py` (add route + import, after line 130)
- Modify: `daemon/tests/test_server_specialists.py`
- Modify: `daemon/src/reachy_ducky_daemon/project.py` (add `github_repo` accessor if not already owner/name-split)

**Pre-check:** read `daemon/src/reachy_ducky_daemon/project.py` and confirm `Project.github_repo` is `"<owner>/<repo>"` or similar. If parsing is needed, this task includes a small `_split_github_repo(repo: str) -> tuple[str, str]` helper + its own tests.

**Step 1: Failing tests**

```python
def test_pr_reviewer_endpoint_explicit_pr_number(tmp_path: Path) -> None:
    # ... mirror test_plan_reviewer_endpoint shape, POST
    #     /specialists/pr-reviewer with {"name": "pr-reviewer",
    #     "project_slug": "repo", "pr_number": 42}, mock subprocess for
    #     all gh/git calls, assert 200 + body["name"] == "pr-reviewer".


def test_pr_reviewer_endpoint_auto_detect(tmp_path: Path) -> None:
    # Same but no pr_number — mock git rev-parse + gh pr list.


def test_pr_reviewer_endpoint_unknown_slug_returns_404(tmp_path: Path) -> None:
    ...


def test_pr_reviewer_endpoint_response_shape(tmp_path: Path) -> None:
    # Shape: {name, summary, flags}. Same as plan_reviewer's response-shape test.


def test_pr_reviewer_endpoint_invokes_brain_once(tmp_path: Path) -> None:
    ...


def test_pr_reviewer_endpoint_project_without_github_repo_returns_400(tmp_path: Path) -> None:
    """A project with github_repo=None can't do PR review — 400 with a useful detail."""
    ...
```

**Step 2:** Run — route doesn't exist → 404.

**Step 3: Implementation** in `server.py` after the plan_reviewer route:

```python
from .specialists.pr_reviewer import PRReviewer

@app.post("/specialists/pr-reviewer", response_model=SpecialistResponse)
async def pr_reviewer_route(req: SpecialistRequest) -> SpecialistResponse:
    try:
        brain = registry.brain_for(req.project_slug)
        repo = registry.path_for(req.project_slug)
        project = registry.project_for(req.project_slug)  # add accessor if missing
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"unknown project: {req.project_slug}",
        ) from None
    if not project.github_repo:
        raise HTTPException(
            status_code=400,
            detail=f"project '{req.project_slug}' has no github_repo configured",
        )
    owner, repo_name = project.github_repo.split("/", 1)
    return await PRReviewer(
        brain=brain, repo=repo, owner=owner, repo_name=repo_name,
    ).review(pr_number=req.pr_number)
```

**Step 4–5:** Run tests, commit:

```bash
git commit -m "feat(server): POST /specialists/pr-reviewer route

Resolves project → repo path + owner/repo from config → PRReviewer.
400 when the project has no github_repo configured; 404 for unknown
slugs; 200 for happy + graceful-fail paths."
```

---

### Task 5.2: `DaemonClient.pr_reviewer(...)` method

**Files:**
- Modify: `app/src/reachy_ducky_app/daemon_client.py` (after line 125)
- Modify: `app/tests/test_daemon_client.py`

**Step 1–5** parallel to plan_reviewer's client pattern (lines 110–125). Method signature:

```python
async def pr_reviewer(
    self,
    *,
    project_slug: str,
    pr_number: int | None = None,
) -> SpecialistResponse:
    """POST ``/specialists/pr-reviewer`` and get a PR review envelope."""
    req = SpecialistRequest(
        name="pr-reviewer", project_slug=project_slug, pr_number=pr_number,
    )
    r = await self._http.post(
        f"{self._base}/specialists/pr-reviewer",
        json=req.model_dump(),
        headers=self._headers(),
        timeout=120.0,
    )
    r.raise_for_status()
    return SpecialistResponse.model_validate(r.json())
```

Test via `pytest-httpx` following the existing `test_daemon_client.py:136` pattern.

Commit:

```bash
git commit -m "feat(app): DaemonClient.pr_reviewer() for app → daemon wire"
```

---

## Milestone 6 — Integration, docs, CI

### Task 6.1: Gated live-PR integration smoke

**Files:**
- Modify: `daemon/tests/test_specialist_pr_reviewer.py` (append)

**Step 1:** Write `@pytest.mark.integration` test targeting stable closed PR #46 (pytest-asyncio bump). Gated on `REACHY_DUCKY_RUN_INTEGRATION=1` per plan_reviewer's pattern (test_specialist_plan_reviewer.py:295–322). Uses real `gh` + `ClaudeSDKBrain.with_tools(...)`.

**Steps 2–5:** Run with env var set, confirm it works end-to-end, commit:

```bash
git commit -m "test(specialists/pr-reviewer): gated live-PR integration smoke"
```

---

### Task 6.2: Docs updates

**Files:**
- Modify: `CLAUDE.md` (add `gh` CLI to Prereqs section + `GH_TOKEN` note)
- Modify: `README.md` (if it lists daemon prereqs)
- Modify: `docs/plans/2026-04-21-reachy-ducky-design.md` §12 (mark pr-reviewer as Phase B landed)

Content:

```markdown
### Prereqs for the daemon's Pattern B brain

[existing content]

- **`gh` CLI (2.x)** — required for the `pr-reviewer` specialist's pre-fetch
  path. Authenticates via `GH_TOKEN` (reuse the same
  `GITHUB_PERSONAL_ACCESS_TOKEN`) or an interactive `gh auth login`.
```

Commit:

```bash
git commit -m "docs: note gh CLI prereq for pr-reviewer; mark pr-reviewer landed"
```

---

## Done / exit criteria

When every task above is committed on `pr-reviewer-specialist` and the full-branch quality gate passes:

```bash
uv run ruff check . \
  && uv run ruff format --check . \
  && uv run mypy --strict daemon/src app/src menubar/src protocol/src daemon/tests protocol/tests menubar/tests app/tests \
  && uv run pyright \
  && uv run bandit -ll -r daemon/src app/src menubar/src protocol/src \
  && uv run pytest -q --cov
```

Coverage ≥ 90%. Then: push, open PR, wait for Augment + Codex reviews, `@codex review` after each push, merge when both are addressed. Close #50 manually with a comment if the redaction follow-up is not resolved in this PR (it won't be — it's a separate issue).
