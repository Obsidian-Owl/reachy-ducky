"""PreToolUse security gate for the brain's SDK-dispatched tools.

The SDK's `PreToolUse` hook lets us inspect and veto a tool call before it runs.
This module exports :func:`security_gate`, an async callback matching the SDK's
``HookCallback`` signature, that enforces two rules at the tool boundary:

1. ``Bash`` — allow only a fixed set of read-only ``git`` subcommands, with no
   shell compounding (``;``, ``&&``, ``||``, pipes, subshells, backticks).
2. ``Read`` / ``Glob`` / ``Grep`` — reject any ``file_path`` / ``path`` /
   ``pattern`` matching the secret-glob blocklist, checked against both the
   basename and the full relative path.

Other tools pass through unchanged; the gate guards only the four listed above.

Wiring (not performed here; see the brain options factory in Task 3.4):

    ClaudeAgentOptions(
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Bash|Read|Glob|Grep", hooks=[security_gate]),
            ],
        },
        ...
    )
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import PurePosixPath

from claude_agent_sdk import (
    HookContext,
    HookJSONOutput,
    PreToolUseHookInput,
)

__all__ = ["security_gate"]

# Only these read-only `git` subcommands are permitted via Bash.
# The anchored regex rejects anything after the subcommand keyword unless
# followed by whitespace or end-of-string, so `gitpush` / `git-log` don't slip.
_BASH_ALLOWLIST = re.compile(
    r"^\s*git\s+"
    r"(status|diff|log|show|branch|rev-parse|ls-files|ls-tree|describe|rev-list)"
    r"(\s|$)"
)

# Shell metacharacters that would let a command escape the allowlist by
# chaining a second command. We reject any Bash input containing any of these,
# even if the head looks like an allowed `git` subcommand.
_COMPOUND_MARKERS: tuple[str, ...] = (";", "&&", "||", "|", "`", "$(")

# Glob patterns for paths that must never be read by the brain. Mirrors the
# `FsTool` denylist semantics from `.claude/settings.json`.
_SECRET_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "secrets/**",
    "credentials*",
)

# The tool-input keys that carry a path or pattern we need to screen.
_PATH_INPUT_KEYS: tuple[str, ...] = ("file_path", "path", "pattern")

# Tools whose input we inspect; anything else is approved pass-through.
_GUARDED_PATH_TOOLS: frozenset[str] = frozenset({"Read", "Glob", "Grep"})


def _allow() -> HookJSONOutput:
    """Return an empty SyncHookJSONOutput — the SDK reads this as 'allow'."""
    return {}


def _deny(reason: str) -> HookJSONOutput:
    """Return a PreToolUse deny decision carrying `reason` back to the model."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }


def _is_allowed_bash(command: str) -> tuple[bool, str]:
    """Return (allowed, reason_if_denied) for a proposed Bash command."""
    if not command:
        return False, "empty Bash command"
    for marker in _COMPOUND_MARKERS:
        if marker in command:
            return False, (
                f"compound shell construct {marker!r} rejected; "
                "only a single read-only git command is allowed"
            )
    if not _BASH_ALLOWLIST.match(command):
        return False, (
            "only read-only git subcommands are allowed "
            "(status, diff, log, show, branch, rev-parse, ls-files, "
            "ls-tree, describe, rev-list)"
        )
    return True, ""


def _matched_secret_pattern(path: str) -> str | None:
    """Return the first secret glob matching `path`, or None.

    Each pattern is tested against both the full path (for patterns like
    ``secrets/**``) and the basename (for patterns like ``.env`` that should
    match regardless of leading directories).
    """
    if not path:
        return None
    basename = PurePosixPath(path).name
    for pattern in _SECRET_PATTERNS:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(basename, pattern):
            return pattern
    return None


async def security_gate(
    input_data: PreToolUseHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> HookJSONOutput:
    """PreToolUse hook: enforce read-only Bash + secret-path blocklist.

    Returns an empty dict to allow the tool call, or a deny decision with a
    reason the SDK surfaces back to Claude so it can choose a different path.
    """
    del tool_use_id, context  # not used; kept for HookCallback signature parity

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {}) or {}

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if not isinstance(command, str):
            return _deny("Bash command must be a string")
        ok, reason = _is_allowed_bash(command)
        return _allow() if ok else _deny(reason)

    if tool_name in _GUARDED_PATH_TOOLS:
        for key in _PATH_INPUT_KEYS:
            candidate = tool_input.get(key)
            if not isinstance(candidate, str):
                continue
            matched = _matched_secret_pattern(candidate)
            if matched is not None:
                return _deny(f"{tool_name} path {candidate!r} matches secret pattern {matched!r}")
        return _allow()

    return _allow()
