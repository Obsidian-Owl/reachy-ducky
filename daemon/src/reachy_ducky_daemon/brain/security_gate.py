"""PreToolUse security gate for the brain's SDK-dispatched tools.

The SDK's `PreToolUse` hook lets us inspect and veto a tool call before it runs.
This module exports :func:`security_gate`, an async callback matching the SDK's
``HookCallback`` signature, that enforces two rules at the tool boundary:

1. ``Bash`` — allow only a fixed set of read-only ``git`` subcommands, with no
   shell compounding (``;``, ``&&``, ``||``, pipes, subshells, backticks,
   redirections, background, embedded newline).
2. ``Read`` / ``Glob`` / ``Grep`` — reject any ``file_path`` / ``path`` /
   ``pattern`` matching the secret-glob blocklist, checked against both the
   basename and the full relative path. For ``Glob``, also screen the
   ``pattern`` field with a substring heuristic for secret-suggesting tokens.

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

# Python 3 str regex uses Unicode whitespace: \s matches NBSP (U+00A0),
# ideographic space (U+3000), etc. — but NOT zero-width space (U+200B).
# This means:
#   "git\u00a0status"  → allowed (NBSP is \s, consistent with normal space)
#   "git\u200bstatus"  → denied (ZWSP isn't \s; regex fails at the `\s+`)
# Both behaviors lock-tested below.
#
# Only these read-only `git` subcommands are permitted via Bash.
# The anchored regex rejects anything after the subcommand keyword unless
# followed by whitespace or end-of-string, so `gitpush` / `git-log` don't slip.
#
# NOT on this list on purpose: `branch`. Though `git branch` alone lists
# branches (read), `git branch -d/-D/-m/-M/-c/-C/--set-upstream-to` all
# mutate refs. Since `_is_allowed_bash` only validates the subcommand
# token (not its flags), listing `branch` would let those mutating forms
# slip through the "read-only" gate. Callers that need the current
# branch should use `rev-parse --abbrev-ref HEAD`; listing remote
# branches can use `for-each-ref` if added later.
_BASH_ALLOWLIST = re.compile(
    r"^\s*git\s+" r"(status|diff|log|show|rev-parse|ls-files|ls-tree|describe|rev-list)" r"(\s|$)"
)

# Shell metacharacters that would let a command escape the allowlist by
# chaining a second command, redirecting output, or running in the background.
# We reject any Bash input containing any of these, even if the head looks
# like an allowed `git` subcommand. `&` as a substring also covers `&&`
# (already listed explicitly for readability) and catches trailing-background.
_COMPOUND_MARKERS: tuple[str, ...] = (
    ";",
    "&&",
    "||",
    "&",
    "|",
    "`",
    "$(",
    ">",
    "<",
    "\n",
)

# Flags on allowed subcommands that either write files, escape the configured
# scope, or inject arbitrary config that turns other flags into RCE. Denied
# regardless of subcommand. Checked against each whitespace-separated token:
# deny if token == prefix OR token starts with ``prefix=``.
#
# Rationale per flag:
#   -o / --output*       — git log/show/diff can write formatted output to
#                          an arbitrary file. "read-only" must mean no writes.
#   -c                   — config override. Lets the caller inject
#                          ``core.textconv``/``diff.*.textconv``/``ext-diff``
#                          entries that are then invoked as commands by
#                          git log/show/diff = RCE.
#   -C                   — run as-if from a different directory: scope escape.
#   --git-dir            — explicit alternative .git dir: scope escape.
#   --work-tree          — explicit alternative worktree: scope escape.
#   --exec               — runs an external command (e.g. git fetch --exec).
#   --upload-pack /      — server-side variants for fetch/push; the target is
#   --receive-pack         attacker-redirectable, making them RCE-adjacent.
#   --textconv           — invokes the configured textconv filter = arbitrary
#                          process execution driven by repo config.
#   --ext-diff           — invokes the configured external diff driver =
#                          same arbitrary-process hazard.
_DENIED_FLAG_PREFIXES: tuple[str, ...] = (
    "-o",
    "--output",
    "--output-indicator",
    "--output-indicator-context",
    "--output-indicator-new",
    "--output-indicator-old",
    "-c",
    "-C",
    "--git-dir",
    "--work-tree",
    "--exec",
    "--upload-pack",
    "--receive-pack",
    "--textconv",
    "--ext-diff",
)

# Patterns use fnmatch semantics (Python stdlib), NOT shell/zsh glob:
#   * matches everything including path separators
#   ** is treated identically to * (no recursive-directory meaning)
#   Patterns are matched against both the full path AND the basename.
# If you port to pathlib.PurePath.match or shell, re-verify every pattern —
# in particular `.env.*` behaves differently (pathlib's * won't match leading .).
#
# Mirrors the deny list in `.claude/settings.json`.
_SECRET_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "secrets/**",
    "credentials*",
)

# Substring heuristics for Glob `pattern` fields: denies shell-style globs
# like `**/*.env` or `secrets/**/*.yaml` that the fnmatch-based secret path
# check would not catch when the caller passes the glob as a pattern (not a
# literal path). Applied case-insensitively.
_SECRET_PATTERN_SUBSTRINGS: tuple[str, ...] = (
    "id_rsa",
    ".env",
    ".pem",
    ".key",
    "secret",
    "credential",
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


def _has_denied_flag(command: str) -> tuple[bool, str]:
    """Return (denied, matched_flag) scanning each whitespace-separated token.

    Matches a token exactly against any entry in :data:`_DENIED_FLAG_PREFIXES`,
    and also matches the ``prefix=value`` form (e.g. ``--output=/tmp/x``,
    ``-c core.textconv=/bin/sh``). The ``=`` form is required because git
    accepts both ``--output file`` and ``--output=file``.
    """
    for token in command.split():
        for prefix in _DENIED_FLAG_PREFIXES:
            if token == prefix or token.startswith(prefix + "="):
                return True, prefix
    return False, ""


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
    denied, flag = _has_denied_flag(command)
    if denied:
        return False, (
            f"write-capable or scope-escaping flag {flag!r} rejected; "
            "the gate allows only read-only git subcommands with their "
            "read-only flags"
        )
    if not _BASH_ALLOWLIST.match(command):
        return False, (
            "only read-only git subcommands are allowed "
            "(status, diff, log, show, rev-parse, ls-files, "
            "ls-tree, describe, rev-list)"
        )
    return True, ""


def _matched_secret_pattern(path: str) -> str | None:
    """Return the first secret glob matching `path`, or None.

    Matching is case-insensitive (macOS APFS is case-insensitive by default, so
    ``.ENV`` / ``ID_RSA`` / ``Credentials.json`` resolve to the same files as
    their lowercase forms). Each pattern is tested against both the full path
    (for patterns like ``secrets/**``) and the basename (for patterns like
    ``.env`` that should match regardless of leading directories). The
    *original* pattern (not the lowercased form) is returned so the deny
    reason stays readable.
    """
    if not path:
        return None
    lowered = path.lower()
    basename = PurePosixPath(lowered).name
    for pattern in _SECRET_PATTERNS:
        lowered_pat = pattern.lower()
        if fnmatch.fnmatch(lowered, lowered_pat) or fnmatch.fnmatch(basename, lowered_pat):
            return pattern
    return None


def _glob_pattern_is_suspicious(pattern: str) -> tuple[bool, str]:
    """Return (True, needle) if a Glob pattern contains a secret-suggesting substring.

    Heuristic only; case-insensitive. Catches patterns like ``**/*.env`` and
    ``secrets/**/*.yaml`` whose literal string wouldn't match the fnmatch-based
    secret blocklist but which clearly intend to harvest secrets.
    """
    lowered = pattern.lower()
    for needle in _SECRET_PATTERN_SUBSTRINGS:
        if needle in lowered:
            return True, needle
    return False, ""


async def security_gate(
    input_data: PreToolUseHookInput,
    _tool_use_id: str | None,
    _context: HookContext,
) -> HookJSONOutput:
    """PreToolUse hook: enforce read-only Bash + secret-path blocklist.

    Returns an empty dict to allow the tool call, or a deny decision with a
    reason the SDK surfaces back to Claude so it can choose a different path.
    """
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {}) or {}

    if tool_name == "Bash":
        raw_command = tool_input.get("command", "")
        # Normalize any non-string shape to empty so `_is_allowed_bash` denies
        # cleanly (list/int/None would otherwise crash the `in`/regex checks).
        command = raw_command if isinstance(raw_command, str) else ""
        ok, reason = _is_allowed_bash(command)
        return _allow() if ok else _deny(reason)

    if tool_name == "Glob":
        pattern_val = tool_input.get("pattern")
        if isinstance(pattern_val, str):
            suspicious, needle = _glob_pattern_is_suspicious(pattern_val)
            if suspicious:
                return _deny(
                    f"Glob pattern {pattern_val!r} contains secret-suggesting substring {needle!r}"
                )

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
