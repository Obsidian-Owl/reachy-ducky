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
from pathlib import Path
from typing import TypedDict

__all__ = [
    "_GITLEAKS_TIMEOUT_SECONDS",
    "RedactionError",
    "redact",
]

_GITLEAKS_TIMEOUT_SECONDS = 30.0


class RedactionError(RuntimeError):
    """Raised when redaction cannot run to completion.

    Intentionally a :class:`RuntimeError` so specialists can catch
    broadly. The ``str(exc)`` carries enough context for the
    diagnostic response body.
    """


class _Finding(TypedDict):
    """Narrow view of a gitleaks finding — only the fields we consume."""

    RuleID: str
    StartLine: int
    EndLine: int
    StartColumn: int
    EndColumn: int


class _Span(TypedDict):
    """A splice target — either one finding or a composite of overlapping findings.

    ``rule_ids`` is the ordered, deduplicated list of rule IDs that matched
    somewhere inside the span. For a single finding it's ``[RuleID]``; for a
    composite built from overlapping findings it carries each contributing
    rule in first-occurrence order. The marker rendered at splice time is
    ``[REDACTED:{','.join(rule_ids)}]``.
    """

    rule_ids: list[str]
    StartLine: int
    EndLine: int
    StartColumn: int
    EndColumn: int


def redact(text: str, *, cwd: Path) -> tuple[str, list[str]]:
    """Return ``(redacted_text, deduplicated_rule_ids)``.

    Invokes ``gitleaks stdin`` with ``--redact`` (so raw secrets never
    appear in the JSON output we parse) and splices
    ``[REDACTED:<RuleID>]`` into ``text`` at each reported position.
    Rule IDs in the returned list are deduplicated, order-preserved
    (first occurrence wins).

    ``cwd`` is the project directory gitleaks runs inside — required so
    the config-precedence chain picks up any repo-local
    ``.gitleaks.toml`` (the "what's a secret" rules stay unified with
    ``lefthook pre-commit``'s ``gitleaks protect --staged``). Without
    this, gitleaks would inherit the daemon's CWD and silently skip
    repo-local allowlists. Required kwarg specifically so callers can
    never accidentally forget — the daemon might be started from any
    directory, but the specialists always know their project root.

    Raises :class:`RedactionError` on missing binary, subprocess
    timeout, non-zero exit, malformed JSON, unexpected JSON shape,
    malformed findings, or coordinates out of range.
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
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GITLEAKS_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise RedactionError(f"gitleaks binary not found on PATH: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RedactionError(f"gitleaks stdin timeout after {_GITLEAKS_TIMEOUT_SECONDS}s") from exc
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

    if not parsed:
        return text, []

    # Narrow: only entries with the five required int/str fields are
    # processed. Anything malformed becomes a RedactionError — we can't
    # safely splice without trustworthy coordinates, and silently
    # dropping findings would risk leaving secrets in.
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
            raise RedactionError(f"gitleaks finding missing required field: {exc}") from exc

    lines = text.split("\n")

    # Per-finding validation before any splicing. Keeps every malformed-
    # finding failure mode in one place and stops later overlap-merging
    # from dealing with coordinate garbage.
    for f in findings:
        _validate_finding(f, lines)

    # Merge overlapping findings into composite spans so the descending-
    # splice order is stable. Disjoint-sort alone only preserves stability
    # when intervals don't overlap; if gitleaks emits two findings that
    # share any position (same token matching two rules, nested matches),
    # splicing one mutates the coordinate space the next was measured in
    # and can leave a secret fragment behind.
    spans = _merge_overlapping_findings(findings)

    # Splice from the end so earlier splices don't shift later indices.
    for span in sorted(spans, key=lambda s: (s["StartLine"], s["StartColumn"]), reverse=True):
        marker = f"[REDACTED:{','.join(span['rule_ids'])}]"
        start_line_idx = span["StartLine"] - 1
        end_line_idx = span["EndLine"] - 1
        start_col_idx = span["StartColumn"] - 1
        # gitleaks EndColumn is end-inclusive 1-indexed; Python slice
        # end is exclusive 0-indexed, so EndColumn maps straight through.
        end_col_idx = span["EndColumn"]

        if start_line_idx == end_line_idx:
            line = lines[start_line_idx]
            lines[start_line_idx] = line[:start_col_idx] + marker + line[end_col_idx:]
        else:
            # Multi-line: collapse the whole range to a single marker,
            # preserving any prefix on the start line and any suffix on
            # the end line. Inner + end lines drop.
            prefix = lines[start_line_idx][:start_col_idx]
            suffix = lines[end_line_idx][end_col_idx:]
            lines[start_line_idx] = prefix + marker + suffix
            del lines[start_line_idx + 1 : end_line_idx + 1]

    # Dedup rule IDs in emission order (first occurrence wins). Spans
    # are built in ascending (StartLine, StartColumn) order so iterating
    # them preserves gitleaks' natural top-to-bottom emission.
    seen: dict[str, None] = {}
    for span in spans:
        for rid in span["rule_ids"]:
            seen.setdefault(rid, None)

    return "\n".join(lines), list(seen.keys())


def _validate_finding(f: _Finding, lines: list[str]) -> None:
    """Raise :class:`RedactionError` if ``f`` has coordinates we can't trust.

    Centralises the "fail-closed on malformed coordinates" policy — if
    gitleaks and our text disagree (line-ending / unicode-width mismatch,
    inverted range, out-of-range column) we refuse to splice rather than
    risk leaving a secret fragment behind. Python slicing would silently
    clamp negatives or oversized indices otherwise.
    """
    # Line-range: must be in-bounds AND StartLine <= EndLine.
    start_line_idx = f["StartLine"] - 1
    end_line_idx = f["EndLine"] - 1
    if not (0 <= start_line_idx < len(lines)) or not (0 <= end_line_idx < len(lines)):
        raise RedactionError(
            f"gitleaks finding line {f['StartLine']}-{f['EndLine']} "
            f"out of range (text has {len(lines)} lines)"
        )
    if f["StartLine"] > f["EndLine"]:
        raise RedactionError(
            f"gitleaks finding has inverted line range "
            f"{f['StartLine']}>{f['EndLine']} (StartLine must be ≤ EndLine)"
        )

    # Column ordering semantics differ between single-line and multi-line
    # findings: for same-line findings, EndColumn ≥ StartColumn is required;
    # for multi-line findings each column applies to its own line so no
    # ordering relationship holds between them. Only the ≥ 1 lower bound
    # is always required.
    if f["StartColumn"] < 1 or f["EndColumn"] < 1:
        raise RedactionError(
            f"gitleaks finding has invalid column range "
            f"{f['StartColumn']}-{f['EndColumn']} (columns must be ≥ 1)"
        )
    if f["StartLine"] == f["EndLine"] and f["EndColumn"] < f["StartColumn"]:
        raise RedactionError(
            f"gitleaks finding has invalid column range "
            f"{f['StartColumn']}-{f['EndColumn']} "
            f"(EndColumn must be ≥ StartColumn on the same line)"
        )

    # Column bounds against actual line lengths. +1 on the upper bound
    # because EndColumn is 1-indexed end-inclusive; a finding that covers
    # the full line reports EndColumn == len(line).
    start_line = lines[start_line_idx]
    end_line = lines[end_line_idx]
    if f["StartLine"] == f["EndLine"]:
        if f["EndColumn"] > len(start_line) + 1:
            raise RedactionError(
                f"gitleaks finding EndColumn {f['EndColumn']} "
                f"exceeds line {f['StartLine']} length ({len(start_line)})"
            )
    else:
        if f["StartColumn"] > len(start_line) + 1:
            raise RedactionError(
                f"gitleaks finding StartColumn {f['StartColumn']} "
                f"exceeds line {f['StartLine']} length ({len(start_line)})"
            )
        if f["EndColumn"] > len(end_line) + 1:
            raise RedactionError(
                f"gitleaks finding EndColumn {f['EndColumn']} "
                f"exceeds line {f['EndLine']} length ({len(end_line)})"
            )


def _merge_overlapping_findings(findings: list[_Finding]) -> list[_Span]:
    """Return composite spans where any overlapping findings are merged.

    Walks findings in ascending ``(StartLine, StartColumn)`` order; each
    span either starts a new composite or extends the previous one if
    its start position falls inside (or touches) the previous span's
    end position. The returned list is ordered by start position, which
    matches the order gitleaks emits its findings — useful for the flag
    list's first-occurrence dedup.

    Merging is the Codex-review fix (PR #52) for the correctness hazard
    where splicing one finding mutates the coordinate space of an
    overlapping sibling. After merging, all splice intervals are
    disjoint and descending-sort gives stable indices.
    """
    ordered = sorted(findings, key=lambda f: (f["StartLine"], f["StartColumn"]))
    merged: list[_Span] = []
    for f in ordered:
        if merged and _spans_overlap(merged[-1], f):
            prev = merged[-1]
            # Extend the end bound if this finding reaches further.
            if (f["EndLine"], f["EndColumn"]) > (prev["EndLine"], prev["EndColumn"]):
                prev["EndLine"] = f["EndLine"]
                prev["EndColumn"] = f["EndColumn"]
            if f["RuleID"] not in prev["rule_ids"]:
                prev["rule_ids"].append(f["RuleID"])
        else:
            merged.append(
                {
                    "rule_ids": [f["RuleID"]],
                    "StartLine": f["StartLine"],
                    "EndLine": f["EndLine"],
                    "StartColumn": f["StartColumn"],
                    "EndColumn": f["EndColumn"],
                }
            )
    return merged


def _spans_overlap(a: _Span, b: _Finding) -> bool:
    """True if ``a`` ends at or after ``b`` starts (inclusive positions).

    Caller guarantees ``a`` starts at or before ``b`` (list is pre-sorted
    ascending). Both coordinate pairs are 1-indexed end-inclusive, so
    ``(EndLine, EndColumn)`` is the last covered position and
    ``(StartLine, StartColumn)`` is the first — adjacency counts as
    overlap so we merge ``[1..5]`` + ``[5..10]`` into ``[1..10]``
    (they share column 5).
    """
    return (a["EndLine"], a["EndColumn"]) >= (b["StartLine"], b["StartColumn"])
