# Secret Redaction Across Specialists — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Implement design doc §10's pre-brain secret redaction. Pipe every specialist-assembled prompt through `gitleaks stdin` before `brain.query()`, splice `[REDACTED:<RuleID>]` markers in place of matches, surface findings as `redacted:<RuleID>` flags. Fail-closed: if redaction can't run, abort the review with a diagnostic `SpecialistResponse` rather than risk leaking secrets. **Closes #50.**

**Architecture:** Shared helper module `daemon/src/reachy_ducky_daemon/specialists/redaction.py` exports `redact(text: str) -> tuple[str, list[str]]`, which invokes `gitleaks stdin` as a subprocess, parses its JSON findings, and splices markers inline using the reported 1-indexed (StartLine, StartColumn, EndLine, EndColumn) coordinates. Each specialist's `review()` wraps the call in a try/except; on `RedactionError` the specialist returns a 200 `SpecialistResponse` with `flags=["redaction-failed"]` and no brain call fires.

**Tech Stack:**
- Python 3.12, `uv` workspace
- `gitleaks` (Go binary, already on PATH locally and trusted by `lefthook pre-commit`)
- `subprocess` (list-form, `shell=False`, stdin-piped, 30s timeout)
- `pytest`, `unittest.mock.patch` for subprocess mocking
- Pydantic v2 `SpecialistResponse` (no protocol changes — flags list already carries machine-tags)

**Conventions:**
- TDD per task: failing test → run (confirm failure shape) → minimal impl → run (pass) → commit.
- Subprocess list-form, `check=False`, stderr surfaced in exception messages.
- Unit tests mock `subprocess.run`; no real `gitleaks` invocation at the unit tier.
- One `@pytest.mark.integration` smoke test exercises the real binary, gated on `REACHY_DUCKY_RUN_INTEGRATION=1`.
- Per-task gate before commit: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict <touched> && uv run pytest -q`.
- Full-branch gate before push: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict daemon/src app/src menubar/src protocol/src daemon/tests protocol/tests menubar/tests app/tests && uv run pyright && uv run bandit -ll -r daemon/src app/src menubar/src protocol/src && uv run pytest -q --cov`. Helper ≥ 95% coverage; overall ≥ 90%.

**Reference skills:** @superpowers:test-driven-development, @superpowers:verification-before-completion

**Template to mirror:** `daemon/src/reachy_ducky_daemon/specialists/pr_reviewer.py` — subprocess helper shape (`_run_gh`), error-as-data pattern, module layout, docstring style. The helper here is a single-function module (not a class) because it's stateless.

---

## Milestone 1 — Redaction helper module

### Task 1.1: Scaffold `redaction.py` + `RedactionError` + subprocess invocation

**Files:**
- Create: `daemon/src/reachy_ducky_daemon/specialists/redaction.py`
- Create: `daemon/tests/test_specialist_redaction.py`

**Step 1: Write the failing tests**

Create `daemon/tests/test_specialist_redaction.py`:

```python
"""Tests for :func:`redact`.

Unit tier mocks ``subprocess.run`` at the boundary — no real ``gitleaks``
invocation. A gated integration test at the bottom exercises the real
binary. Mirrors the shape of ``test_specialist_pr_reviewer.py``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from reachy_ducky_daemon.specialists.redaction import (
    _GITLEAKS_TIMEOUT_SECONDS,
    RedactionError,
    redact,
)


def _ok(stdout: str) -> subprocess.CompletedProcess[str]:
    """Shorthand for a successful ``CompletedProcess`` with canned stdout."""
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_gitleaks_timeout_matches_gh_timeout_precedent() -> None:
    """_GITLEAKS_TIMEOUT_SECONDS aligns with pr_reviewer's 30s gh timeout.

    Keeps the "subprocess operation time budget" consistent across
    specialist helpers — any future bump is a deliberate edit in one place.
    """
    assert _GITLEAKS_TIMEOUT_SECONDS == 30.0


def test_redact_passes_clean_text_through_unchanged() -> None:
    """Empty JSON findings → input text unchanged, empty flag list."""
    with patch("subprocess.run", return_value=_ok("[]")):
        out, flags = redact("just benign text\nno secrets here\n")

    assert out == "just benign text\nno secrets here\n"
    assert flags == []


def test_redact_invokes_gitleaks_stdin_with_expected_flags() -> None:
    """Argv contract: gitleaks stdin --no-banner --exit-code 0 --report-format json --report-path - --redact."""
    with patch("subprocess.run", return_value=_ok("[]")) as m:
        redact("anything")

    argv = m.call_args.args[0]
    assert argv[:2] == ["gitleaks", "stdin"]
    assert "--no-banner" in argv
    assert "--exit-code" in argv
    assert "0" in argv  # exit-code value
    assert "--report-format" in argv
    assert "json" in argv
    assert "--report-path" in argv
    assert "-" in argv  # stdout sentinel
    assert "--redact" in argv

    call_kwargs = m.call_args.kwargs
    assert call_kwargs["check"] is False
    assert call_kwargs["text"] is True
    assert call_kwargs["timeout"] == _GITLEAKS_TIMEOUT_SECONDS
    # Input is piped via stdin.
    assert call_kwargs["input"] == "anything"


def test_redact_raises_on_missing_binary() -> None:
    """FileNotFoundError from subprocess (binary missing) → RedactionError."""
    with patch("subprocess.run", side_effect=FileNotFoundError("gitleaks")):
        with pytest.raises(RedactionError, match="gitleaks"):
            redact("anything")


def test_redact_raises_on_timeout() -> None:
    """TimeoutExpired → RedactionError with 'timeout' in the message."""
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="gitleaks", timeout=30.0),
    ):
        with pytest.raises(RedactionError, match="timeout"):
            redact("anything")


def test_redact_raises_on_non_zero_exit() -> None:
    """Non-zero exit (gitleaks crashed) → RedactionError with stderr in message."""
    bad = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="gitleaks: fatal: bad config"
    )
    with patch("subprocess.run", return_value=bad):
        with pytest.raises(RedactionError, match="fatal: bad config"):
            redact("anything")


def test_redact_raises_on_malformed_json() -> None:
    """Malformed JSON on stdout → RedactionError."""
    with patch("subprocess.run", return_value=_ok("not json")):
        with pytest.raises(RedactionError, match="JSON"):
            redact("anything")


def test_redact_raises_on_non_list_json() -> None:
    """Unexpected JSON shape (object instead of list) → RedactionError."""
    with patch("subprocess.run", return_value=_ok('{"oops": true}')):
        with pytest.raises(RedactionError, match="list"):
            redact("anything")
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest daemon/tests/test_specialist_redaction.py -v`
Expected: `ModuleNotFoundError: No module named 'reachy_ducky_daemon.specialists.redaction'` (module doesn't exist yet).

**Step 3: Write minimal implementation**

Create `daemon/src/reachy_ducky_daemon/specialists/redaction.py`:

```python
"""Pre-brain secret redaction using the ``gitleaks`` CLI.

Specialists assemble prompts from user-controlled surfaces (plan text,
diffs, PR bodies, review comments) and pass them to ``brain.query()``.
Any secret pasted into one of those surfaces would otherwise flow
straight to Claude. :func:`redact` pipes the assembled prompt through
``gitleaks stdin``, splices ``[REDACTED:<RuleID>]`` over each match,
and returns the sanitized text plus a deduplicated list of rule IDs
the caller can emit as ``redacted:<rule_id>`` flags.

Coherence with the rest of the stack: gitleaks honours any
``.gitleaks.toml`` in the current directory via the same precedence
chain as lefthook's ``gitleaks protect --staged``, so "what counts as
a secret" stays unified across commit-time and brain-time.

Fail policy: on any subprocess failure, malformed output, or missing
binary, :func:`redact` raises :class:`RedactionError`. Callers route
to a fail-closed diagnostic ``SpecialistResponse`` — no brain call
fires when redaction can't run.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 — list-form read-only gitleaks only; no shell
from typing import TypedDict

__all__ = [
    "_GITLEAKS_TIMEOUT_SECONDS",
    "RedactionError",
    "redact",
]

_GITLEAKS_TIMEOUT_SECONDS = 30.0


class RedactionError(RuntimeError):
    """Raised when redaction cannot run to completion.

    Intentionally a ``RuntimeError`` so specialists can catch broadly.
    The str(exc) carries enough context for the diagnostic response.
    """


class _Finding(TypedDict):
    """Narrow view of a gitleaks finding — only the fields we consume."""

    RuleID: str
    StartLine: int
    EndLine: int
    StartColumn: int
    EndColumn: int


def redact(text: str) -> tuple[str, list[str]]:
    """Return ``(redacted_text, deduplicated_rule_ids)``.

    Invokes ``gitleaks stdin`` with ``--redact`` (so raw secrets never
    appear in the JSON output we parse) and splices
    ``[REDACTED:<RuleID>]`` into ``text`` at each reported position.
    Rule IDs in the returned list are deduplicated, order-preserved
    (first occurrence wins).

    Raises :class:`RedactionError` on missing binary, subprocess
    timeout, non-zero exit, malformed JSON, or unexpected JSON shape.
    """
    try:
        proc = subprocess.run(  # noqa: S603  # nosec B603 B607 — list form, read-only gitleaks only
            [
                "gitleaks",
                "stdin",
                "--no-banner",
                "--exit-code",
                "0",
                "--report-format",
                "json",
                "--report-path",
                "-",
                "--redact",
            ],
            input=text,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GITLEAKS_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise RedactionError(f"gitleaks binary not found on PATH: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RedactionError(
            f"gitleaks stdin timed out after {_GITLEAKS_TIMEOUT_SECONDS}s"
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise RedactionError(f"gitleaks subprocess failed: {exc}") from exc

    if proc.returncode != 0:
        stderr = proc.stderr.strip() or "non-zero exit"
        raise RedactionError(f"gitleaks stdin failed: {stderr}")

    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RedactionError(f"gitleaks stdin emitted unparseable JSON: {exc}") from exc

    if not isinstance(parsed, list):
        raise RedactionError(
            f"gitleaks stdin returned non-list JSON (unexpected shape): {type(parsed).__name__}"
        )

    # Splice + dedup land in Task 1.2. For now: pass text through, empty flags.
    if not parsed:
        return text, []
    # Placeholder — next task implements splicing.
    return text, []
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest daemon/tests/test_specialist_redaction.py -v`
Expected: all 8 tests PASS (pass-through test passes because findings list is empty; splicing tests come in Task 1.2).

Also run the type-check:
`uv run mypy --strict daemon/src daemon/tests`
Expected: no new errors.

Bandit check:
`uv run bandit -ll -r daemon/src`
Expected: no medium/high findings.

**Step 5: Commit**

```bash
git add daemon/src/reachy_ducky_daemon/specialists/redaction.py daemon/tests/test_specialist_redaction.py
git commit -m "feat(specialists): scaffold redaction helper with RedactionError

Module entrypoint for pre-brain secret redaction per design doc §10 and
issue #50. Mirrors pr_reviewer's subprocess helper shape: list-form args,
check=False, stderr-as-diagnostic, 30s timeout, no shell=True. Raises
RedactionError on any subprocess failure mode so specialists can route
to a fail-closed diagnostic response rather than risk leaking secrets.

Splicing logic lands in the next task."
```

---

### Task 1.2: Splicing logic — single-line, multi-line, dedup

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/specialists/redaction.py`
- Modify: `daemon/tests/test_specialist_redaction.py`

**Step 1: Write the failing tests**

Append to `daemon/tests/test_specialist_redaction.py`:

```python
def _finding_json(findings: list[dict[str, object]]) -> str:
    """Render a list of gitleaks-shaped finding dicts as JSON."""
    import json as _json

    return _json.dumps(findings)


def test_redact_splices_single_finding() -> None:
    """One github-pat → marker replaces the match, rule id returned."""
    # Use Python concatenation so the source file doesn't contain a
    # contiguous ``ghp_[A-Za-z0-9]{36}`` that lefthook's gitleaks-staged
    # would catch at commit time. The runtime value is a 40-char
    # shape-matching fake (``ghp_`` + 36 chars); mock subprocess returns
    # canned findings regardless.
    input_text = "github_pat = ghp_" + ("x" * 36) + "\n"  # gitleaks:allow
    findings = [
        {
            "RuleID": "github-pat",
            "StartLine": 1,
            "EndLine": 1,
            "StartColumn": 15,  # 1-indexed; gitleaks' empirically-observed offset
            "EndColumn": 54,
        }
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        out, flags = redact(input_text)

    # The shape-matching "token" built by concatenation above must be gone.
    assert "ghp_" + ("x" * 36) not in out  # gitleaks:allow
    assert "[REDACTED:github-pat]" in out
    assert flags == ["github-pat"]


def test_redact_deduplicates_same_rule() -> None:
    """Three github-pat findings → three splices, one flag entry (dedup)."""
    # Concatenation form again — source text stays unscannable while the
    # runtime value has three shape-matching 40-char tokens on separate lines.
    input_text = (
        "a = ghp_" + ("a" * 36) + "\n"  # gitleaks:allow
        "b = ghp_" + ("b" * 36) + "\n"  # gitleaks:allow
        "c = ghp_" + ("c" * 36) + "\n"  # gitleaks:allow
    )
    findings = [
        {"RuleID": "github-pat", "StartLine": i, "EndLine": i, "StartColumn": 5, "EndColumn": 44}
        for i in (1, 2, 3)
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        out, flags = redact(input_text)

    assert out.count("[REDACTED:github-pat]") == 3
    assert flags == ["github-pat"]


def test_redact_preserves_rule_order_across_types() -> None:
    """Multiple distinct rules → flag list preserves first-occurrence order."""
    input_text = "line1 with github\nline2 with aws\n"
    findings = [
        {
            "RuleID": "github-pat",
            "StartLine": 1,
            "EndLine": 1,
            "StartColumn": 12,
            "EndColumn": 18,
        },
        {
            "RuleID": "aws-access-key",
            "StartLine": 2,
            "EndLine": 2,
            "StartColumn": 12,
            "EndColumn": 15,
        },
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        _, flags = redact(input_text)

    assert flags == ["github-pat", "aws-access-key"]


def test_redact_collapses_multi_line_finding_to_single_marker() -> None:
    """Multi-line finding (e.g. a private-key block) → one marker for the whole range.

    Uses generic placeholder lines rather than a real-looking
    ``-----BEGIN PRIVATE KEY-----`` block — which would trip gitleaks'
    ``private-key`` rule at commit time on this source file itself.
    The mocked subprocess returns canned findings at coords we pick, so
    the input text's content is arbitrary.
    """
    input_text = (
        "context before\n"
        "PLACEHOLDER_LINE_1\n"
        "PLACEHOLDER_LINE_2_WITH_KEY_MATERIAL\n"
        "PLACEHOLDER_LINE_3\n"
        "context after\n"
    )
    findings = [
        {
            "RuleID": "private-key",
            "StartLine": 2,
            "EndLine": 4,
            "StartColumn": 1,
            "EndColumn": 18,  # length of "PLACEHOLDER_LINE_3"
        }
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        out, flags = redact(input_text)

    assert "PLACEHOLDER_LINE_1" not in out
    assert "PLACEHOLDER_LINE_2_WITH_KEY_MATERIAL" not in out
    assert "PLACEHOLDER_LINE_3" not in out
    assert "[REDACTED:private-key]" in out
    # Bookend context preserved.
    assert "context before" in out
    assert "context after" in out
    assert flags == ["private-key"]


def test_redact_splices_without_shifting_earlier_findings() -> None:
    """Two findings on the same line: marker for the later one doesn't shift the earlier."""
    input_text = "alpha AAAAAAAA beta BBBBBBBB end\n"
    findings = [
        {"RuleID": "rule-a", "StartLine": 1, "EndLine": 1, "StartColumn": 7, "EndColumn": 14},
        {"RuleID": "rule-b", "StartLine": 1, "EndLine": 1, "StartColumn": 21, "EndColumn": 28},
    ]
    with patch("subprocess.run", return_value=_ok(_finding_json(findings))):
        out, _ = redact(input_text)

    assert "AAAAAAAA" not in out
    assert "BBBBBBBB" not in out
    assert out.count("[REDACTED:rule-a]") == 1
    assert out.count("[REDACTED:rule-b]") == 1
    # Bookend words still present and in order.
    assert out.index("alpha") < out.index("[REDACTED:rule-a]") < out.index("beta")
    assert out.index("beta") < out.index("[REDACTED:rule-b]") < out.index("end")
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest daemon/tests/test_specialist_redaction.py -v`
Expected: the 5 new tests FAIL (helper still passes text through unchanged); the 8 Task-1.1 tests continue to PASS.

**Step 3: Write minimal implementation**

Replace the placeholder tail of `redact()` with real splicing logic. The new function body (replace everything from `# Splice + dedup land in Task 1.2.` to the end):

```python
    if not parsed:
        return text, []

    # Narrow: only entries with the five required int+str fields are
    # processed. Anything malformed becomes a RedactionError since we
    # can't safely splice without trustworthy coordinates.
    findings: list[_Finding] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            raise RedactionError("gitleaks finding is not an object")
        try:
            findings.append(
                {
                    "RuleID": str(entry["RuleID"]),
                    "StartLine": int(entry["StartLine"]),
                    "EndLine": int(entry["EndLine"]),
                    "StartColumn": int(entry["StartColumn"]),
                    "EndColumn": int(entry["EndColumn"]),
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RedactionError(
                f"gitleaks finding missing required field: {exc}"
            ) from exc

    # Splice from the end so earlier splices don't shift later indices.
    # Sort by (StartLine, StartColumn) descending.
    findings.sort(key=lambda f: (f["StartLine"], f["StartColumn"]), reverse=True)

    lines = text.split("\n")
    for f in findings:
        marker = f"[REDACTED:{f['RuleID']}]"
        start_line_idx = f["StartLine"] - 1
        end_line_idx = f["EndLine"] - 1
        start_col_idx = f["StartColumn"] - 1
        end_col_idx = f["EndColumn"]  # gitleaks EndColumn is end-inclusive 1-indexed
        # Defensive: clamp to the line list we have. Out-of-range indices
        # would mean gitleaks and our line-split disagree about content —
        # fail closed rather than silently skip.
        if not (0 <= start_line_idx < len(lines)) or not (0 <= end_line_idx < len(lines)):
            raise RedactionError(
                f"gitleaks finding line {f['StartLine']}-{f['EndLine']} "
                f"out of range (text has {len(lines)} lines)"
            )

        if start_line_idx == end_line_idx:
            line = lines[start_line_idx]
            lines[start_line_idx] = line[:start_col_idx] + marker + line[end_col_idx:]
        else:
            # Multi-line: collapse the whole range to a single marker,
            # keeping any prefix on the start line and any suffix on the
            # end line. Inner lines are removed entirely.
            prefix = lines[start_line_idx][:start_col_idx]
            suffix = lines[end_line_idx][end_col_idx:]
            lines[start_line_idx] = prefix + marker + suffix
            # Drop the inner + end lines.
            del lines[start_line_idx + 1 : end_line_idx + 1]

    # Dedup rule IDs in original occurrence order. The original findings
    # list is in gitleaks' emission order (typically top-to-bottom), so
    # iterate the un-reversed list for flag ordering.
    seen: dict[str, None] = {}
    for entry in parsed:
        # We already validated shape above; just grab the RuleID.
        rid = str(entry["RuleID"])  # type: ignore[index]
        if rid not in seen:
            seen[rid] = None

    return "\n".join(lines), list(seen.keys())
```

**Step 4: Run tests**

Run: `uv run pytest daemon/tests/test_specialist_redaction.py -v`
Expected: all 13 tests PASS (8 from Task 1.1 + 5 new).

Run: `uv run mypy --strict daemon/src daemon/tests`
Expected: clean.

**Step 5: Commit**

```bash
git add daemon/src/reachy_ducky_daemon/specialists/redaction.py daemon/tests/test_specialist_redaction.py
git commit -m "feat(specialists/redaction): splice findings with dedup + multi-line support

Processes gitleaks findings from end to start so earlier splices don't
shift later indices. Multi-line findings (private-key blocks) collapse
the whole (StartLine, EndLine) range to a single marker, preserving
prefix on StartLine and suffix on EndLine. Rule IDs returned in
first-occurrence order, deduplicated.

Empirical column convention (pr #51 brainstorm): gitleaks emits
1-indexed StartColumn and end-inclusive 1-indexed EndColumn, so the
Python slice is line[StartColumn-1:EndColumn]. Finding coords out of
range fail closed — we don't know what text gitleaks actually scanned
vs what we split, so silently skipping could leave secrets in.

Per plan docs/plans/2026-04-23-secret-redaction-specialists.md Task 1.2."
```

---

### Task 1.3: Gated integration smoke — real `gitleaks`

**Files:**
- Modify: `daemon/tests/test_specialist_redaction.py` (append)

**Step 1: Write the failing test**

Append:

```python
# ---------------------------------------------------------------------------
# Integration (gated) — real gitleaks binary
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_redact_real_gitleaks_catches_synthetic_pat() -> None:
    """End-to-end smoke: pipe a synthetic GitHub PAT through real gitleaks.

    Gated on ``REACHY_DUCKY_RUN_INTEGRATION=1`` — this is the only test
    in this module that requires the actual binary on PATH. Mirrors the
    gating pattern from ``test_specialist_pr_reviewer.test_pr_reviewer_live_claude``.

    Uses a shape-matching but clearly-fake PAT value so we don't risk
    tripping secret scanners in the source tree itself.
    """
    if not os.environ.get("REACHY_DUCKY_RUN_INTEGRATION"):
        pytest.skip("set REACHY_DUCKY_RUN_INTEGRATION=1 to run")

    synthetic = (
        "benign preamble\n"
        "github_pat = ghp_" + "A" * 36 + "\n"
        "postamble line\n"
    )

    redacted, flags = redact(synthetic)

    assert "ghp_" + "A" * 36 not in redacted
    assert "[REDACTED:github-pat]" in redacted
    assert "github-pat" in flags
    # Non-secret content preserved.
    assert "benign preamble" in redacted
    assert "postamble line" in redacted
```

**Step 2: Run the gated test directly (with real binary)**

Run: `REACHY_DUCKY_RUN_INTEGRATION=1 uv run pytest daemon/tests/test_specialist_redaction.py::test_redact_real_gitleaks_catches_synthetic_pat -v`
Expected: PASS.

Also confirm it skips cleanly when ungated:
Run: `uv run pytest daemon/tests/test_specialist_redaction.py -v`
Expected: 13 passed, 1 skipped.

**Step 3: Commit**

```bash
git add daemon/tests/test_specialist_redaction.py
git commit -m "test(specialists/redaction): gated real-gitleaks integration smoke

Pipes a synthetic github_pat through the actual binary on PATH and
asserts the marker appears + rule id flagged. Gated on
REACHY_DUCKY_RUN_INTEGRATION=1 so CI unit runs don't gain a gitleaks
prereq. Completes Milestone 1.

Per plan Task 1.3."
```

---

## Milestone 2 — PlanReviewer integration

### Task 2.1: Wire `redact()` into `PlanReviewer.review` with fail-closed path

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/specialists/plan_reviewer.py` (lines 237–255, the `review()` method)
- Modify: `daemon/tests/test_specialist_plan_reviewer.py` (append)

**Step 1: Write the failing tests**

Append to `daemon/tests/test_specialist_plan_reviewer.py`:

```python
# ---------------------------------------------------------------------------
# Redaction integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_reviewer_redacts_prompt_before_brain_query(
    repo_with_plan_and_drift: Path,
) -> None:
    """Prompt reaching the brain is the redacted version; rule ids flow to flags."""
    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=repo_with_plan_and_drift)

    # Redact strips the "sensitive" token and tags it as 'fake-rule'.
    def _fake_redact(text: str) -> tuple[str, list[str]]:
        return text.replace("sensitive", "[REDACTED:fake-rule]"), ["fake-rule"]

    # Inject a "sensitive" token into the prompt so we can observe it being
    # scrubbed. The plan file is written fresh by the fixture.
    plan_path = repo_with_plan_and_drift / "docs" / "plans" / "foo.md"
    plan_path.write_text(plan_path.read_text() + "\nsensitive token here\n")

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
    from reachy_ducky_daemon.specialists.redaction import RedactionError

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
```

Note: the first test mutates the fixture to inject the sensitive token — acceptable because the fixture is a fresh `tmp_path` per test.

The second test uses `patch` keyed on `reachy_ducky_daemon.specialists.plan_reviewer.redact` — so the production module must import `redact` at module scope (not inside the method) for this patch target to work.

**Step 2: Run tests to verify they fail**

Run: `uv run pytest daemon/tests/test_specialist_plan_reviewer.py -v`
Expected: 2 new tests FAIL — `redact` isn't imported into `plan_reviewer` yet, so the patch target doesn't exist and/or the production code doesn't call it. The mutation test's brain will receive the raw prompt including "sensitive token here".

**Step 3: Modify `plan_reviewer.py`**

Add import at the top of the file, after the existing `from reachy_ducky_daemon.brain.interface import BrainInterface`:

```python
from reachy_ducky_daemon.specialists.redaction import RedactionError, redact
```

Update `__all__` to include neither (both are internal; `PlanReviewer` is the exported entrypoint).

Replace the `review()` method body (currently lines 237–255):

```python
    async def review(self) -> SpecialistResponse:
        """Assemble the review prompt, redact secrets, dispatch to the brain.

        Exactly one ``brain.query`` call per invocation — but only when
        redaction succeeds. A :class:`RedactionError` short-circuits to
        a fail-closed diagnostic response; no brain call fires, no
        secret leaks.
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

        try:
            redacted, rule_ids = redact(prompt)
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
```

**Step 4: Run tests**

Run: `uv run pytest daemon/tests/test_specialist_plan_reviewer.py -v`
Expected: all existing tests + the 2 new ones PASS.

Run: `uv run mypy --strict daemon/src daemon/tests`
Expected: clean.

**Step 5: Commit**

```bash
git add daemon/src/reachy_ducky_daemon/specialists/plan_reviewer.py daemon/tests/test_specialist_plan_reviewer.py
git commit -m "feat(specialists/plan-reviewer): redact prompt before brain.query

Wraps the brain.query call in a try/except RedactionError. On success,
rule ids flow into SpecialistResponse.flags as redacted:<rule_id>. On
failure, no brain call fires — we return a 200 response with a
'redaction-failed' flag and a summary explaining the abort. Follows
the design doc §10 'fail-closed' posture so a broken gitleaks install
cannot leak secrets through the specialist.

Per plan Task 2.1."
```

---

## Milestone 3 — PRReviewer integration (happy + diagnostic paths)

### Task 3.1: Wire `redact()` into both `PRReviewer.review` paths

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/specialists/pr_reviewer.py` (the `review()` happy path around line 548, and `_diagnostic_response` around line 565)
- Modify: `daemon/tests/test_specialist_pr_reviewer.py` (append)

**Step 1: Write the failing tests**

Append to `daemon/tests/test_specialist_pr_reviewer.py`:

```python
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

    def _fake_redact(text: str) -> tuple[str, list[str]]:
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
    # Existing derived flags still present — union not replacement.
    assert "ci-red" in response.flags
    assert "has-unresolved-comments" in response.flags


@pytest.mark.asyncio
async def test_pr_reviewer_redacts_diagnostic_path(tmp_path: Path) -> None:
    """Diagnostic (no-PR-found) path also redacts — branch_error / find_error strings can embed credentials."""
    side_effect = _mock_by_argv(
        {
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): _ok("feat-x\n"),
            # gh pr list fails with an auth error that embeds a credential-ish URL.
            ("gh", "pr", "list"): subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="remote: url https://x:sensitive_token_here@github.com/...",
            ),
        }
    )

    def _fake_redact(text: str) -> tuple[str, list[str]]:
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
    """RedactionError on the happy path → 200 response with redaction-failed flag, no brain call."""
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
            side_effect=RedactionError("gitleaks timed out"),
        ),
    ):
        response = await reviewer.review(pr_number=42)

    assert response.name == "pr-reviewer"
    assert "redaction-failed" in response.flags
    assert "timed out" in response.summary.lower()
    assert len(brain.calls) == 0, "brain.query must not fire when redaction fails"
```

**Step 2: Run tests — confirm failures**

Run: `uv run pytest daemon/tests/test_specialist_pr_reviewer.py -v`
Expected: 3 new tests FAIL (redaction isn't wired into pr_reviewer yet).

**Step 3: Modify `pr_reviewer.py`**

Add the import at the top of the module, after `from reachy_ducky_daemon.brain.interface import BrainInterface`:

```python
from reachy_ducky_daemon.specialists.redaction import RedactionError, redact
```

Refactor `review()`. Replace the tail (from `prompt = _assemble_prompt(...)` through the end of the method) with:

```python
        prompt = _assemble_prompt(
            pr=pr_meta,
            diff=diff,
            comments=comments,
            check_runs=check_runs,
            diff_error=diff_err,
            comments_error=comments_err,
            check_runs_error=check_runs_err,
        )
        base_flags = _derive_flags(pr=pr_meta, comments=comments, check_runs=check_runs)
        return await self._query_with_redaction(prompt=prompt, base_flags=base_flags)
```

Replace `_diagnostic_response` with:

```python
    async def _diagnostic_response(
        self,
        *,
        branch: str,
        branch_error: str | None,
        find_error: str | None,
    ) -> SpecialistResponse:
        """Dispatch the diagnostic prompt (redacted) and wrap the brain's reply."""
        prompt = _assemble_diagnostic_prompt(
            branch=branch or "unknown",
            branch_error=branch_error,
            find_error=find_error,
        )
        base_flags = _derive_flags(pr={}, comments=[], check_runs=[])
        return await self._query_with_redaction(prompt=prompt, base_flags=base_flags)
```

Add the shared helper method on `PRReviewer`:

```python
    async def _query_with_redaction(
        self,
        *,
        prompt: str,
        base_flags: list[str],
    ) -> SpecialistResponse:
        """Redact, then query brain. Fail-closed on RedactionError.

        Shared by both the happy review path and the diagnostic path
        so every prompt that reaches ``brain.query()`` is run through
        ``redact()`` first (design doc §10). RedactionError short-
        circuits to a 200 SpecialistResponse with a ``redaction-failed``
        flag and no brain call.
        """
        try:
            redacted, rule_ids = redact(prompt)
        except RedactionError as exc:
            return SpecialistResponse(
                name=_SPECIALIST_NAME,
                summary=(
                    f"Redaction unavailable: {exc}. Aborting review to "
                    "prevent secret leaks — re-run once the redactor is back."
                ),
                flags=[*base_flags, "redaction-failed"],
            )

        brain_resp = await self._brain.query(BrainRequest(user_utterance=redacted))
        return SpecialistResponse(
            name=_SPECIALIST_NAME,
            summary=brain_resp.text,
            flags=[*base_flags, *(f"redacted:{rid}" for rid in rule_ids)],
        )
```

The `base_flags` carry the objective CI / comments / no-pr-found flags so the failure path still communicates the state we could determine without the brain.

**Step 4: Run tests**

Run: `uv run pytest daemon/tests/test_specialist_pr_reviewer.py -v`
Expected: all existing tests + 3 new ones PASS.

Run: `uv run mypy --strict daemon/src daemon/tests`
Expected: clean.

**Step 5: Commit**

```bash
git add daemon/src/reachy_ducky_daemon/specialists/pr_reviewer.py daemon/tests/test_specialist_pr_reviewer.py
git commit -m "feat(specialists/pr-reviewer): redact prompts before brain.query on both paths

Introduces _query_with_redaction shared by review() happy path and
_diagnostic_response. Every prompt that reaches brain.query is scanned
first; rule ids flow into SpecialistResponse.flags as redacted:<rid>.
RedactionError short-circuits to a 200 response with redaction-failed
appended to the existing base flags — no brain call fires, no secret
leaks.

The diagnostic path is covered too because branch_error / find_error /
stderr strings can legitimately embed credentials (auth URLs with
embedded tokens, etc.). Design doc §10 posture: fail-closed across the
board.

Completes Milestone 3. Per plan Task 3.1."
```

---

## Milestone 4 — Docs

### Task 4.1: Update design doc §10 + link the closing issue

**Files:**
- Modify: `docs/plans/2026-04-21-reachy-ducky-design.md` (§10)

**Step 1: Locate the §10 block**

Current §10 text (from prior read) says redaction runs via `gitleaks`/`trufflehog` "pre-send". We need to replace the "not implemented" shape with the "landed" shape.

**Step 2: Replace the block**

Find the bullet starting with `- **Secret redaction pre-send.**` in §10 and replace it with:

```markdown
- **Secret redaction pre-send (landed 2026-04-23).** `gitleaks stdin`
  runs over every specialist-assembled prompt before it reaches
  `brain.query()`; matches are spliced with `[REDACTED:<RuleID>]` and
  surface as `redacted:<rule_id>` flags on the `SpecialistResponse`.
  Fail-closed: a broken `gitleaks` install aborts the review with a
  `redaction-failed` flag — no brain call fires. Shared helper:
  `daemon/src/reachy_ducky_daemon/specialists/redaction.py`. Gitleaks
  config precedence mirrors lefthook's pre-commit hook, so "what's a
  secret" stays unified across commit-time and brain-time. See
  `docs/plans/2026-04-23-secret-redaction-specialists.md` and closed
  issue #50.
```

**Step 3: Commit**

```bash
git add docs/plans/2026-04-21-reachy-ducky-design.md
git commit -m "docs: mark §10 secret redaction landed; link implementation plan + #50

Per plan Task 4.1."
```

---

## Done / exit criteria

When every task is committed on `secret-redaction` and the full-branch gate passes:

```bash
uv run ruff check . \
  && uv run ruff format --check . \
  && uv run mypy --strict daemon/src app/src menubar/src protocol/src daemon/tests protocol/tests menubar/tests app/tests \
  && uv run pyright \
  && uv run bandit -ll -r daemon/src app/src menubar/src protocol/src \
  && uv run pytest -q --cov
```

Coverage ≥ 90% overall, helper ≥ 95%.

Then:

1. `git push -u origin secret-redaction`
2. `gh pr create --title "feat(specialists): secret redaction across specialists (closes #50)" --body ...` with `Closes #50` in the body.
3. Wait for Augment; address any valid findings in follow-up commits.
4. Merge when green.
5. Issue #50 auto-closes on merge via `Closes #50` in the PR body.

### Acceptance criteria check (from issue #50)

- [x] Helper module with ≥95% coverage → verified by final `pytest --cov` run
- [x] Applied in `PlanReviewer` pre-query step → Task 2.1
- [x] Applied in `PRReviewer` pre-query step → Task 3.1 (both paths)
- [x] Integration test: synthetic secret emerges redacted → Task 1.3 (gated real-gitleaks smoke)
- [x] Unit tests: no-secret pass-through, single match, multi match, flag contract → Tasks 1.1–1.2
- [x] Design doc §10 updated with chosen approach → Task 4.1
