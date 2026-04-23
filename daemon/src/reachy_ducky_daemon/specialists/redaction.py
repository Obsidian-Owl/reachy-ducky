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

    # Splice from the end so earlier splices don't shift later indices.
    # Sort by (StartLine, StartColumn) descending.
    for f in sorted(findings, key=lambda f: (f["StartLine"], f["StartColumn"]), reverse=True):
        marker = f"[REDACTED:{f['RuleID']}]"
        start_line_idx = f["StartLine"] - 1
        end_line_idx = f["EndLine"] - 1
        start_col_idx = f["StartColumn"] - 1
        # gitleaks EndColumn is end-inclusive 1-indexed; Python slice
        # end is exclusive 0-indexed, so EndColumn maps straight through.
        end_col_idx = f["EndColumn"]

        # Defensive: clamp to the line list we have. Out-of-range indices
        # mean gitleaks and our line-split disagree about content — fail
        # closed rather than silently skip.
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
            # preserving any prefix on the start line and any suffix on
            # the end line. Inner + end lines drop.
            prefix = lines[start_line_idx][:start_col_idx]
            suffix = lines[end_line_idx][end_col_idx:]
            lines[start_line_idx] = prefix + marker + suffix
            del lines[start_line_idx + 1 : end_line_idx + 1]

    # Dedup rule IDs in original emission order (first occurrence wins).
    # Iterate the un-sorted findings list so the output flag list reflects
    # gitleaks' natural top-to-bottom emission.
    seen: dict[str, None] = {}
    for f in findings:
        seen.setdefault(f["RuleID"], None)

    return "\n".join(lines), list(seen.keys())
