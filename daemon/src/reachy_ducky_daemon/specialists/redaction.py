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

    # Splicing lands in Task 1.2. For now: empty findings → pass through;
    # non-empty findings → placeholder pass-through (will be replaced).
    if not parsed:
        return text, []
    return text, []
