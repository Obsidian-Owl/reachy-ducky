# PlanReviewer Polish Sweep Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Close 5 open `TODO(#N)` issues in `daemon/src/reachy_ducky_daemon/specialists/plan_reviewer.py` (#2, #3, #4, #5, #8) as a single coherent sweep — promote the underscore import, harden the plan-discovery security boundary, surface read-error diagnostics, cap prompt size, and fix the missing fallback-banner/test — before Phase B adds more specialists built on the same patterns.

**Architecture:** 5 milestones, each a single commit on one branch (`plan-reviewer-polish-sweep`), all shipped in one PR. Ordering is dictated by dependency (#4's rename must land before the other fixes reference the public symbol; #8's security filter in `_discover` reshapes what `_collect_plans` sees; #5/#2/#3 are all independent of each other once #4/#8 are in).

**Tech Stack:** Python 3.12, `uv` workspace, Pydantic v2, pytest + `subprocess` for the existing `@pytest.mark.asyncio` specialist tests. No new dependencies.

**Issues closed:**

| Milestone | Issue | Subject |
|---|---|---|
| M1 | #4 | Promote `_list_plans` → `list_plans` |
| M2 | #8 | `_discover` filters paths that resolve outside `base` (escaped symlinks) |
| M3 | #5 | `_collect_plans` surfaces per-file read errors as `=== UNREADABLE PLANS ===` |
| M4 | #2 | Constructor-kwarg size caps on `_assemble_prompt` (per-file + total) |
| M5 | #3 | `_capture_diff` prepends fallback banner; add missing merge-base-absent test |

**Conventions used throughout:**
- TDD per task: failing test → run it (fails with the expected shape) → minimal implementation → run it (passes) → commit.
- Per-task gate before commit: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict daemon/src daemon/tests && uv run pytest -q daemon/tests/test_brain_plans_mcp.py daemon/tests/test_specialist_plan_reviewer.py`.
- Full-branch gate before push: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict daemon/src app/src menubar/src protocol/src daemon/tests protocol/tests menubar/tests app/tests && uv run pyright && uv run bandit -ll -r daemon/src app/src menubar/src protocol/src && uv run pytest -q --cov`. Coverage floor 90%.
- One commit per milestone. Conventional commits (`feat:` / `fix:` / `refactor:` / `test:`).

**Reference skills:** @superpowers:test-driven-development, @superpowers:verification-before-completion

**Key design decisions (locked during 2026-04-23 brainstorm):**
- **#4**: clean rename, no `_list_plans` compat alias. Only callers: `plan_reviewer.py` (via underscore import) + `plans_mcp.py`'s own tests.
- **#8**: fix in `_discover` (not `_collect_plans`). Security property belongs where the resolution happens; keeps the `_read_plan` membership check correct for free.
- **#5**: unreadable files surface as a separate `=== UNREADABLE PLANS ===` section (not inline in `=== PLANS ===`) — easier for the brain's attention to anchor on.
- **#2**: caps are constructor kwargs, not class constants. Defaults: `max_plan_chars=50_000`, `max_total_plan_chars=200_000`. Per-file truncation inside `_collect_plans`; total-budget truncation inside `_assemble_prompt`. Marker: `[... truncated: N chars elided ...]`.
- **#3**: fallback banner is a one-line prefix on the diff text (not a separate `=== DIFF (FALLBACK) ===` section).

**Session scope:** One session is realistic. 5 milestones, small file, no hardware. Expect ~1 hour across all implementer + reviewer dispatches.

---

## Milestone 1 — Promote `_list_plans` → `list_plans` (#4)

Foundational rename. Drops the cross-subpackage underscore import that `plan_reviewer.py` currently relies on.

### Task 1.1: Rename + update callers

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/brain/plans_mcp.py` (rename `_list_plans` → `list_plans`; update `__all__` if the symbol is exported).
- Modify: `daemon/src/reachy_ducky_daemon/specialists/plan_reviewer.py` (change the import + the one call site).
- Modify: `daemon/tests/test_brain_plans_mcp.py` (find+replace `_list_plans` → `list_plans` — mechanical).

**Step 1: Sanity-grep the symbol first**

```bash
grep -rn "_list_plans" daemon/
```

Expected hits:
- `daemon/src/reachy_ducky_daemon/brain/plans_mcp.py` — the definition + internal references.
- `daemon/src/reachy_ducky_daemon/specialists/plan_reviewer.py` — the cross-subpackage import + one call.
- `daemon/tests/test_brain_plans_mcp.py` — tests.

No daemon-side consumers beyond these three files (verify before editing).

**Step 2: Rename in `plans_mcp.py`**

Change `def _list_plans(project_root: Path) -> list[str]:` → `def list_plans(project_root: Path) -> list[str]:`. Update any internal docstrings that mention `_list_plans`. Update `__all__` if the module declares one (check — if not, no change needed).

**Step 3: Update the cross-subpackage import + TODO comment**

In `plan_reviewer.py` around lines 47–55 — the block with the `TODO(#4): promote _list_plans to public list_plans` comment — delete the TODO comment entirely (it's now resolved) and change the import:

```python
from reachy_ducky_daemon.brain.plans_mcp import list_plans
```

Update the one call site (around line 162 in `_collect_plans`): `for rel in _list_plans(repo):` → `for rel in list_plans(repo):`.

**Step 4: Update tests**

In `daemon/tests/test_brain_plans_mcp.py`, find+replace every `_list_plans` → `list_plans` — purely mechanical. Do NOT touch `_read_plan` or `_discover` references; those stay private.

**Step 5: Run tests + gate**

```bash
uv run pytest daemon/tests/test_brain_plans_mcp.py daemon/tests/test_specialist_plan_reviewer.py -q
uv run mypy --strict daemon/src daemon/tests
uv run ruff check .
```

All three must be clean.

**Step 6: Commit**

```bash
git add daemon/src/reachy_ducky_daemon/brain/plans_mcp.py daemon/src/reachy_ducky_daemon/specialists/plan_reviewer.py daemon/tests/test_brain_plans_mcp.py
git commit -m "refactor(brain): promote _list_plans to public list_plans

The cross-subpackage underscore import from plan_reviewer was Python's
'do not touch' convention being bent for convenience. Promoting the
function to public collapses the precedent risk — the second specialist
(pr-reviewer already landed; future ones likely to follow) won't inherit
an ambiguous 'is this off-limits or not' signal.

_read_plan and _discover stay private — they're the security-validated
read path (closes-over project root, blocks path escapes + non-listed
reads) and have no legitimate cross-subpackage consumer.

Resolves the TODO(#4) in plan_reviewer.py:52. Closes #4."
```

---

## Milestone 2 — Symlink-escape filter in `_discover` (#8)

Quietly drop paths that resolve outside `project_root`. Security property lives at the resolution seam, not at the caller.

### Task 2.1: Filter in `_discover`

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/brain/plans_mcp.py` (the `_discover` function).
- Modify: `daemon/tests/test_brain_plans_mcp.py` (new test for escaped symlink).

**Step 1: Write the failing test**

Append to `daemon/tests/test_brain_plans_mcp.py`:

```python
def test_discover_rejects_escaped_symlink(tmp_path: Path) -> None:
    """A symlink under docs/plans/ that resolves outside project_root is dropped.

    Security property: a plan-shaped path whose ``.resolve()`` escapes
    ``base`` cannot be advertised by list_plans or read by read_plan.
    Enforced in _discover so _list_plans + _read_plan both inherit the
    filter for free.
    """
    # Create a real file outside the project root.
    outside = tmp_path / "outside.txt"
    outside.write_text("secret content")

    # Create a project with docs/plans/, and a symlink there pointing OUT.
    project = tmp_path / "project"
    plans_dir = project / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    legitimate = plans_dir / "ok.md"
    legitimate.write_text("# Legitimate plan")
    escape = plans_dir / "escape.md"
    escape.symlink_to(outside)

    # list_plans must NOT advertise escape.md; only ok.md.
    listed = list_plans(project)
    assert "docs/plans/ok.md" in listed
    assert "docs/plans/escape.md" not in listed
    assert not any("outside" in p for p in listed)


def test_read_plan_rejects_escaped_symlink(tmp_path: Path) -> None:
    """read_plan returns 'not a plan' for paths _discover would silently drop.

    Uses the same fixture shape as the discover test but asserts the
    per-file read guard still refuses. No ValueError leaks out of
    read_plan — the fail-closed contract is intact.
    """
    outside = tmp_path / "outside.txt"
    outside.write_text("secret content")
    project = tmp_path / "project"
    plans_dir = project / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    escape = plans_dir / "escape.md"
    escape.symlink_to(outside)

    with pytest.raises(PermissionError, match="not a plan"):
        _read_plan(project, "docs/plans/escape.md")
```

**Step 2: Run tests — expect failure**

```bash
uv run pytest daemon/tests/test_brain_plans_mcp.py -k "escaped_symlink" -v
```

Expected: `test_discover_rejects_escaped_symlink` FAILS because `escape.md` currently appears in `list_plans`'s output; `test_read_plan_rejects_escaped_symlink` may PASS (the existing `relative_to` guard in `_read_plan` should already catch it via its try/except → `PermissionError`). If both fail, great — the fix handles both.

**Step 3: Implement the filter in `_discover`**

In `plans_mcp.py`, change the `_discover` function from:

```python
def _discover(base: Path) -> set[Path]:
    results: set[Path] = set()
    for pattern in _CONVENTIONAL_PATTERNS:
        for hit in base.glob(pattern):
            if hit.is_file():
                results.add(hit.resolve())
    return results
```

To:

```python
def _discover(base: Path) -> set[Path]:
    """..."""  # (keep existing docstring, extend it — see below)
    results: set[Path] = set()
    for pattern in _CONVENTIONAL_PATTERNS:
        for hit in base.glob(pattern):
            if not hit.is_file():
                continue
            resolved = hit.resolve()
            # Reject symlinks whose resolved target escapes ``base``.
            # ``is_relative_to`` (Python 3.9+) is the non-raising form
            # of the same check ``_read_plan`` uses. Filtering here
            # means both list_plans (discovery) and _read_plan
            # (validation) inherit the property for free — no escaping
            # path can be advertised, read, or leaked via diagnostic.
            if not resolved.is_relative_to(base):
                continue
            results.add(resolved)
    return results
```

Extend the docstring to note the new filter. Update the reference in the module docstring if it mentions "symlink escapes are denied" (good place to cite this change).

**Step 4: Run tests — expect pass**

```bash
uv run pytest daemon/tests/test_brain_plans_mcp.py -q
uv run mypy --strict daemon/src daemon/tests
```

Both clean.

**Step 5: Commit**

```bash
git commit -m "fix(brain): _discover filters paths that resolve outside base (#8)

Security property belongs at the resolution seam. A symlink
docs/plans/x.md -> /etc/passwd previously was silently listed by
list_plans (its relative_to(base) would raise ValueError and propagate
up through PlanReviewer._collect_plans → review() → a 500 on the
/specialists/plan-reviewer endpoint). read_plan's existing
relative_to guard caught it one layer later, but discovery leaked
before that.

Filter at _discover using is_relative_to (non-raising). Both
list_plans (what we advertise) and _read_plan's membership check
(what we allow to be read) now inherit the property for free —
escaping paths cannot enter the discover set at all.

Closes #8."
```

---

## Milestone 3 — Unreadable-plan diagnostics (#5)

`_collect_plans` accumulates per-file read errors; `_assemble_prompt` surfaces them as a new section.

### Task 3.1: Accumulate + surface read errors

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/specialists/plan_reviewer.py` (`_collect_plans`, `_assemble_prompt`, `review`).
- Modify: `daemon/tests/test_specialist_plan_reviewer.py` (new test for a non-UTF-8 plan).

**Step 1: Write the failing test**

Append to `daemon/tests/test_specialist_plan_reviewer.py`:

```python
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
    # Write a legitimate plan…
    (plans_dir / "ok.md").write_text("# OK Plan\nreadable body\n")
    # …and a binary blob that isn't valid UTF-8.
    (plans_dir / "bad.md").write_bytes(b"\xff\xfe\x00\x00not utf-8")
    _commit(tmp_path, "plans")

    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=tmp_path)
    await reviewer.review()

    prompt = brain.calls[-1].user_utterance
    assert "# OK Plan" in prompt  # readable plan still present
    assert "=== UNREADABLE PLANS ===" in prompt  # new section present
    assert "bad.md" in prompt  # the failing file's path is named
    # The UTF-8 decode failure shows up as a diagnostic string; exact
    # message depends on Python's error but 'utf-8' substring is stable.
    unreadable_section = prompt.split("=== UNREADABLE PLANS ===", 1)[1]
    assert "bad.md" in unreadable_section
    assert "utf-8" in unreadable_section.lower() or "decode" in unreadable_section.lower()
```

**Step 2: Run test — expect failure**

Expected: FAIL because `=== UNREADABLE PLANS ===` section doesn't exist yet; also the bad file is silently skipped so its path isn't in the prompt at all.

**Step 3: Refactor `_collect_plans` + `_assemble_prompt` + `review`**

Change `_collect_plans` to return a tuple of `(readable, unreadable)`:

```python
def _collect_plans(
    repo: Path,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return ``(readable, unreadable)`` — ``readable`` is ``[(rel, content)]``
    for every plan that loaded cleanly; ``unreadable`` is ``[(rel, err)]``
    for every plan ``list_plans`` advertised but ``read_text`` rejected.

    Both lists are sorted by ``rel``; either may be empty.

    TODO(#5) [resolved]: previously this function silently swallowed
    OSError/UnicodeDecodeError. Now the diagnostic surfaces to the
    brain via the ``=== UNREADABLE PLANS ===`` prompt section (see
    ``_assemble_prompt``).
    """
    readable: list[tuple[str, str]] = []
    unreadable: list[tuple[str, str]] = []
    for rel in list_plans(repo):
        try:
            content = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unreadable.append((rel, f"{type(exc).__name__}: {exc}"))
            continue
        readable.append((rel, content))
    return readable, unreadable
```

Update `review()` to receive both lists:

```python
async def review(self) -> SpecialistResponse:
    branch, branch_error = _current_branch(self._repo)
    plans, unreadable_plans = _collect_plans(self._repo)
    diff, diff_error = _capture_diff(self._repo, branch)

    prompt = _assemble_prompt(
        branch=branch,
        branch_error=branch_error,
        plans=plans,
        unreadable_plans=unreadable_plans,
        diff=diff,
        diff_error=diff_error,
    )
    # ... rest unchanged
```

Update `_assemble_prompt` to accept `unreadable_plans` and emit the section:

```python
def _assemble_prompt(
    branch: str,
    branch_error: str | None,
    plans: list[tuple[str, str]],
    unreadable_plans: list[tuple[str, str]],
    diff: str,
    diff_error: str | None,
) -> str:
    """..."""  # (extend existing docstring)
    parts: list[str] = []
    # ... existing branch + plans blocks unchanged
    # New section — immediately after the plans block, before the diff:
    if unreadable_plans:
        parts.append("=== UNREADABLE PLANS ===")
        parts.append(
            "(These files were discovered under conventional locations but "
            "could not be read. Listed here so you can note them in the "
            "review — they do not participate in drift analysis.)"
        )
        for rel, err in unreadable_plans:
            parts.append(f"--- {rel} ---")
            parts.append(f"(diagnostic: {err})")
        parts.append("")
    # ... existing diff block + directive unchanged
```

**Step 4: Run tests — expect pass**

Existing tests may need small updates because `_collect_plans` now returns a tuple. Check + adjust any direct-call tests. `review()` tests shouldn't need changes (they only inspect the prompt content).

```bash
uv run pytest daemon/tests/test_specialist_plan_reviewer.py -q
uv run mypy --strict daemon/src daemon/tests
```

**Step 5: Commit**

```bash
git commit -m "feat(specialists/plan-reviewer): surface unreadable-plan diagnostics (#5)

_collect_plans previously swallowed OSError/UnicodeDecodeError and
silently dropped the file — the brain couldn't tell 'file listed but
unreadable' from 'file never existed'. Most-likely real case: a
non-UTF-8 plan file confuses the review silently.

Now _collect_plans returns (readable, unreadable) tuples;
_assemble_prompt renders unreadable entries under a new
'=== UNREADABLE PLANS ===' section (separate from the main plans
block so the brain's attention anchors cleanly). Fail-closed contract
intact: no exception escapes review().

Parity with _current_branch and _capture_diff, both of which already
surface errors as '(diagnostic: ...)' in the prompt."
```

---

## Milestone 4 — Size caps on `_assemble_prompt` (#2)

Per-file truncation in `_collect_plans`, total-budget truncation in `_assemble_prompt`. Thresholds are constructor kwargs with defaults.

### Task 4.1: Per-file + total caps

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/specialists/plan_reviewer.py` (`PlanReviewer.__init__`, `_collect_plans`, `_assemble_prompt`).
- Modify: `daemon/tests/test_specialist_plan_reviewer.py` (new tests for per-file + total caps).

**Step 1: Failing tests**

```python
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
        brain=brain, repo=tmp_path, max_plan_chars=50_000, max_total_plan_chars=200_000
    )
    await reviewer.review()

    prompt = brain.calls[-1].user_utterance
    # First 50,000 chars present; the rest elided with the marker.
    assert "[... truncated:" in prompt
    assert "70000" in prompt  # 120k − 50k = 70k elided
    # The full 120k body must NOT appear.
    assert prompt.count("x") <= 55_000  # small fuzzy margin for surrounding text


@pytest.mark.asyncio
async def test_total_cap_drops_remaining_plans(tmp_path: Path) -> None:
    """Plans beyond max_total_plan_chars are replaced by a single summary marker."""
    _init_repo(tmp_path)
    plans_dir = tmp_path / "docs" / "plans"
    plans_dir.mkdir(parents=True)
    # 5 plans × 50k each = 250k, exceeds 200k total cap.
    for i in range(5):
        (plans_dir / f"p{i}.md").write_text("y" * 50_000)
    _commit(tmp_path, "many plans")

    brain = MockBrain()
    reviewer = PlanReviewer(
        brain=brain, repo=tmp_path, max_plan_chars=60_000, max_total_plan_chars=200_000
    )
    await reviewer.review()

    prompt = brain.calls[-1].user_utterance
    # At least one plan must have landed, and at least one must have been skipped.
    # Skipped plans surface under a '[... N plans omitted ...]'-style marker.
    assert "plans omitted" in prompt or "plans elided" in prompt


@pytest.mark.asyncio
async def test_caps_have_sensible_defaults() -> None:
    """Defaults: max_plan_chars=50_000, max_total_plan_chars=200_000.

    Pins the defaults so a future change is deliberate.
    """
    reviewer = PlanReviewer(brain=MockBrain(), repo=Path("/tmp"))
    assert reviewer._max_plan_chars == 50_000  # noqa: SLF001
    assert reviewer._max_total_plan_chars == 200_000  # noqa: SLF001
```

**Step 2: Run tests — expect failure**

`PlanReviewer.__init__` doesn't accept the kwargs; `_collect_plans` and `_assemble_prompt` don't truncate.

**Step 3: Implement**

Constructor kwargs:

```python
class PlanReviewer:
    def __init__(
        self,
        brain: BrainInterface,
        repo: Path,
        *,
        max_plan_chars: int = 50_000,
        max_total_plan_chars: int = 200_000,
    ) -> None:
        """..."""  # (extend docstring)
        self._brain = brain
        self._repo = repo
        self._max_plan_chars = max_plan_chars
        self._max_total_plan_chars = max_total_plan_chars
```

Per-file truncation inside `_collect_plans` (apply AFTER the `read_text` succeeds, BEFORE appending to the readable list):

```python
def _truncate_plan_body(body: str, max_chars: int) -> str:
    """Truncate ``body`` to ``max_chars`` with an inline marker if cut."""
    if len(body) <= max_chars:
        return body
    elided = len(body) - max_chars
    return body[:max_chars] + f"\n[... truncated: {elided} chars elided ...]\n"
```

Hook into `_collect_plans`: pass `max_chars` through from `review()` call site (since the function is a module-level helper, not a method — keep the signature explicit):

```python
def _collect_plans(
    repo: Path,
    max_chars_per_plan: int,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    # ... same as M3 structure, with:
    readable.append((rel, _truncate_plan_body(content, max_chars_per_plan)))
```

Update `review()` to pass the cap: `plans, unreadable = _collect_plans(self._repo, max_chars_per_plan=self._max_plan_chars)`.

Total-budget truncation inside `_assemble_prompt`: after assembling the individual `--- rel ---` + body blocks for each plan, track running char count; when the budget is exhausted, append a marker and stop adding plans.

```python
def _assemble_plans_block(
    plans: list[tuple[str, str]],
    max_total_chars: int,
) -> list[str]:
    """Build the plans section under a total char budget."""
    parts: list[str] = ["=== PLANS ==="]
    if not plans:
        parts.append("(no plan or spec files discovered under conventional locations...)")
        return parts

    used = 0
    included = 0
    for rel, body in plans:
        block = f"--- {rel} ---\n{body.rstrip(chr(10))}\n"
        if used + len(block) > max_total_chars and included > 0:
            remaining = len(plans) - included
            parts.append(
                f"[... {remaining} plan(s) omitted: total body "
                f"budget of {max_total_chars} chars exhausted ...]"
            )
            break
        parts.append(block.rstrip(chr(10)))
        parts.append("")
        used += len(block)
        included += 1
    return parts
```

Call from `_assemble_prompt` in place of the old inline plan-rendering loop. The existing `(no plans)` branch stays.

**Step 4: Run tests — expect pass**

```bash
uv run pytest daemon/tests/test_specialist_plan_reviewer.py -q
uv run mypy --strict daemon/src daemon/tests
```

Existing tests may need a small update where they construct `PlanReviewer` in the integration test at the bottom — add the new kwargs as default (they're keyword-only).

**Step 5: Commit**

```bash
git commit -m "feat(specialists/plan-reviewer): size caps on assembled prompt (#2)

Constructor kwargs max_plan_chars (default 50_000) and
max_total_plan_chars (default 200_000). Per-file truncation inside
_collect_plans appends a visible marker ('[... truncated: N chars
elided ...]'). Total-budget truncation inside _assemble_prompt stops
adding plans once the cumulative budget is exceeded, replacing the
rest with a single '[... N plan(s) omitted ...]' marker.

Rationale: Claude's 200k-token context needs room for the diff + the
brain's response + other sections; concatenating every plan's full
body (this repo's own Phase-A plan is 3000+ lines) risks silent
truncation at the SDK layer. Visible in-prompt truncation is better
than silent mid-sentence cut-off.

Defaults calibrated to real plan sizes in this repo; override via
kwargs if sharp edges appear. YAGNI: no per-project config hook;
change in one place if needed."
```

---

## Milestone 5 — Fallback banner + missing test (#3)

One-line banner on the diff text when `_capture_diff` falls back; add the missing test for the feature-branch-with-no-main-ref case.

### Task 5.1: Banner + test coverage

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/specialists/plan_reviewer.py` (`_capture_diff`).
- Modify: `daemon/tests/test_specialist_plan_reviewer.py` (new test).

**Step 1: Failing test**

The existing test suite has three `_capture_diff` scenarios covered (feature branch success, main with uncommitted, no plans). Missing: feature branch WHERE `main` ref doesn't exist.

```python
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
    (tmp_path / "docs" / "plans" / "foo.md").write_text(
        "# Plan Foo\nUNCOMMITTED_EDIT\n"
    )

    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=tmp_path)
    await reviewer.review()

    prompt = brain.calls[-1].user_utterance
    # Fallback engaged — uncommitted edit surfaces.
    assert "UNCOMMITTED_EDIT" in prompt
    # Banner line sits at the top of the diff section.
    diff_section = prompt.split("=== DIFF ===", 1)[1]
    assert "(fallback:" in diff_section or "fallback:" in diff_section
    assert "working-tree" in diff_section.lower() or "working tree" in diff_section.lower()
```

**Step 2: Run — expect failure**

`(fallback: ...)` banner doesn't exist yet; the test fails at the banner assertion.

**Step 3: Implement banner in `_capture_diff`**

Inside the existing fallback branch (when `git diff main...HEAD` fails and we fall back to `git diff`), prefix the fallback output with a banner. Change the tail of `_capture_diff` from:

```python
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
```

To:

```python
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
            "(fallback: using working-tree-vs-HEAD diff; "
            "merge-base against main was unavailable)\n"
        )
        return banner + fallback.stdout, fallback_err
    return fallback.stdout, fallback_err
```

**Step 4: Run tests — expect pass**

```bash
uv run pytest daemon/tests/test_specialist_plan_reviewer.py -q
uv run mypy --strict daemon/src daemon/tests
```

Existing `test_main_branch_fallback_includes_uncommitted_diff` test should continue to pass — on the main-branch path, `fallback_err` is `None` and the banner isn't prepended. Verify.

**Step 5: Commit**

```bash
git commit -m "feat(specialists/plan-reviewer): banner + test for merge-base fallback (#3)

When `git diff main...HEAD` fails (main ref absent / shallow clone /
whatever), PlanReviewer falls back to `git diff` (working-tree vs
HEAD). Previously the brain couldn't tell that fallback had engaged —
it just saw a diff without context. Now the diff text starts with a
one-line banner:

    (fallback: using working-tree-vs-HEAD diff; merge-base against
    main was unavailable)

Disambiguates 'working-tree diff because on main' from 'working-tree
diff because main ref is absent'. No banner when falling back wasn't
actually a fallback (on-main path), so the common case stays clean.

Also adds the previously-missing test for the feature-branch-
without-main-ref case (the fallback-when-failed-but-had-tried-merge-
base branch, not the on-main branch).

Closes #3."
```

---

## Done / exit criteria

When all 5 milestone commits are on `plan-reviewer-polish-sweep`:

1. Run the full-branch gate:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict daemon/src app/src menubar/src protocol/src daemon/tests protocol/tests menubar/tests app/tests
uv run pyright
uv run bandit -ll -r daemon/src app/src menubar/src protocol/src
uv run pytest -q --cov
```

All clean. Coverage ≥ 90%.

2. `grep -rn "TODO(#[2345])" daemon/` — should return 0 hits (all four in-code TODOs are resolved). `TODO(#8)` should also be gone — #8's TODO was never added as a code comment; it's resolved by the `_discover` filter.

3. Push + open PR:

```bash
git push -u origin plan-reviewer-polish-sweep
gh pr create --base main --title "refactor(specialists): PlanReviewer polish sweep (closes #2, #3, #4, #5, #8)" --body "<summary of each milestone's fix>"
```

4. Augment + Codex review; address + merge; all 5 issues auto-close via the `Closes` lines.

5. Follow-ups NOT in scope: #19 (BrainRequest.include_tools prune), #6 (AppConfig errors), and the hardware-dependent #20 / #23 — still blocked on physical Reachy.
