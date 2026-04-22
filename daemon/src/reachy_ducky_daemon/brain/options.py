"""Brain options factory — assembles :class:`ClaudeAgentOptions` for the thinking brain.

This module wires the previously-built components
(:func:`~reachy_ducky_daemon.brain.security_gate.security_gate`,
:func:`~reachy_ducky_daemon.brain.plans_mcp.plans_mcp_server`) into a single
:class:`ClaudeAgentOptions` object that :class:`ClaudeSDKBrain` will consume in
Task 3.5. Pure config assembly — no live Claude, no subprocess, no filesystem
or network I/O at call time.

Verified SDK shapes (``claude_agent_sdk`` 0.1.64)::

    ClaudeAgentOptions(
        tools: list[str] | ToolsPreset | None = None,
        allowed_tools: list[str] = <factory>,        # *auto-approve* only, not a restrictor
        system_prompt: str | SystemPromptPreset | SystemPromptFile | None = None,
        mcp_servers: dict[str, McpStdioServerConfig | McpSSEServerConfig
                                | McpHttpServerConfig | McpSdkServerConfig]
                       | str | Path = <factory>,
        permission_mode: Literal['default','acceptEdits','plan',
                                 'bypassPermissions','dontAsk','auto'] | None = None,
        disallowed_tools: list[str] = <factory>,
        model: str | None = None,
        cwd: str | Path | None = None,
        add_dirs: list[str | Path] = <factory>,
        hooks: dict[Literal['PreToolUse', ...], list[HookMatcher]] | None = None,
        ...
    )

    HookMatcher(
        matcher: str | None = None,
        hooks: list[HookCallback] = <factory>,
        timeout: float | None = None,
    )

    McpStdioServerConfig = TypedDict({
        'type': NotRequired[Literal['stdio']],       # optional; SDK defaults correctly
        'command': str,
        'args': NotRequired[list[str]],
        'env': NotRequired[dict[str, str]],
    })

The SDK does **not** expand ``${VAR}`` placeholders inside ``env`` — the dict
is handed verbatim to the spawned process. We read
``GITHUB_PERSONAL_ACCESS_TOKEN`` from ``os.environ`` at factory call time and
pass the literal value. An absent token becomes an empty string; the error
surfaces at tool-dispatch time (``github-mcp-server`` rejects unauthenticated
API calls), not at factory build time. Document this in the daemon startup
path: the caller must ensure the env var is set before the daemon starts if
``github_repo`` is provided.

``GITHUB_PERSONAL_ACCESS_TOKEN`` is read from ``os.environ`` on each factory
call. An already-built ``ClaudeAgentOptions`` captures the value at build
time; callers who need to pick up a rotated token must rebuild.

The only "real" toolset restrictor is ``tools=[...]`` (per SDK issue #361 —
``allowed_tools`` is just an auto-approve rule). We also set
``disallowed_tools`` as belt-and-suspenders to cover every write-capable SDK
tool; even if the tools-list mechanism is ever bypassed, writes stay denied.
The SDK's own canonical write-intent regex (``types.py:538``) is
``"Write|MultiEdit|Edit"``, so ``MultiEdit`` is first-class alongside
``Write`` and ``Edit``. We additionally deny ``NotebookEdit`` (Jupyter
write), ``TodoWrite`` (model-owned todo state — outside our read-only
contract), and ``SlashCommand`` (meta-tool that could re-enter write paths
via dispatched slash commands).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
from claude_agent_sdk.types import HookCallback, McpServerConfig, McpStdioServerConfig

from .plans_mcp import plans_mcp_server
from .security_gate import security_gate

__all__ = [
    "DEFAULT_BRAIN_SYSTEM_PROMPT",
    "build_brain_options",
]

# System prompt extends the base "read-only Ducky" instruction with
# tool-surface hints so Claude doesn't need to discover its own capabilities
# via an error-driven probe. Kept terse; concrete tool names map to what
# ``tools=[...]`` below actually admits.
DEFAULT_BRAIN_SYSTEM_PROMPT = (
    "You are Ducky, a read-only rubber-ducky development companion. "
    "You observe and answer; you do not write code. "
    "Be terse. Prefer concrete specifics over vague approval.\n\n"
    "Tools available to you:\n"
    "  - Read / Glob / Grep — inspect the project tree (scoped to cwd + memory).\n"
    "  - Bash — read-only git commands only (status, diff, log, show, branch, "
    "rev-parse, ls-files, ls-tree, describe, rev-list). No chaining, no redirects.\n"
    "  - mcp__plans__* — find and read plan/spec documents.\n"
    "  - mcp__github__* (when configured) — read-only GitHub API access.\n"
    "  - Task — dispatch a constrained subagent for multi-step investigation.\n\n"
    "You cannot Write, Edit, or run arbitrary shell commands. Attempts to do so "
    "will be denied at the hook layer."
)

# Read-only GitHub toolsets. Narrowing or broadening this set changes the
# attack surface of the spawned ``github-mcp-server``; treat as a contract
# reviewed alongside the options factory.
_GITHUB_READ_ONLY_TOOLSETS: tuple[str, ...] = (
    "pull_requests",
    "issues",
    "actions",
    "repos",
)


def _build_github_mcp_config() -> McpStdioServerConfig:
    """Build the stdio spawn config for the external ``github-mcp-server``.

    Reads ``GITHUB_PERSONAL_ACCESS_TOKEN`` from the process environment at
    call time; a missing token becomes an empty string (the spawned server
    will fail authentication, surfacing the error at tool-dispatch time
    rather than silently succeeding).
    """
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    return {
        "type": "stdio",
        "command": "npx",
        "args": [
            "-y",
            "github-mcp-server",
            "--read-only",
            "--toolsets",
            ",".join(_GITHUB_READ_ONLY_TOOLSETS),
        ],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": token},
    }


def build_brain_options(
    *,
    cwd: Path,
    memory_root: Path,
    github_repo: str | None = None,
    system_prompt: str = DEFAULT_BRAIN_SYSTEM_PROMPT,
    model: str = "claude-sonnet-4-6",
) -> ClaudeAgentOptions:
    """Assemble the :class:`ClaudeAgentOptions` that drives the thinking brain.

    ``GITHUB_PERSONAL_ACCESS_TOKEN`` is read from ``os.environ`` on each
    factory call. Already-built ``ClaudeAgentOptions`` captures the value
    at build time; callers who need to pick up a rotated token must rebuild.

    Args:
        cwd: Project root the brain's ``Read``/``Glob``/``Grep``/``Bash`` tools
            are scoped to. Must be a ``Path`` — passing a string is rejected
            so type-confused callers don't slip through.
        memory_root: Additional read-scoped directory (the ducky/human/project
            memory tree). Added via ``add_dirs``. Need not exist at call time;
            the factory performs no filesystem I/O.
        github_repo: If provided, wires the external ``github-mcp-server``
            (npx spawn, read-only, limited toolsets) into ``mcp_servers`` and
            adds ``mcp__github__*`` to the tools allowlist. ``None`` omits
            both. Must match the ``owner/repo`` shape (non-empty owner,
            non-empty repo, exactly one ``/``) when provided.
        system_prompt: Overrides :data:`DEFAULT_BRAIN_SYSTEM_PROMPT`.
        model: Model name passed to the SDK; default matches
            :class:`ClaudeSDKBrain`'s default (``claude-sonnet-4-6``).

    Returns:
        A :class:`ClaudeAgentOptions` with ``tools`` as the real restrictor,
        ``disallowed_tools`` as belt-and-suspenders, ``permission_mode``
        locked to ``"dontAsk"``, the plans (and optionally github) MCP
        servers registered, and ``PreToolUse`` gated by
        :func:`~reachy_ducky_daemon.brain.security_gate.security_gate`.

    Raises:
        TypeError: if ``cwd`` or ``memory_root`` is not a ``Path``.
        ValueError: if ``github_repo`` is provided but does not match
            ``owner/repo`` shape.
    """
    if not isinstance(cwd, Path):
        raise TypeError(f"cwd must be a pathlib.Path, got {type(cwd).__name__}")
    if not isinstance(memory_root, Path):
        raise TypeError(f"memory_root must be a pathlib.Path, got {type(memory_root).__name__}")
    if github_repo is not None:
        parts = github_repo.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"github_repo must be 'owner/repo', got {github_repo!r}")

    tools: list[str] = [
        "Read",
        "Glob",
        "Grep",
        "Bash",
        "Task",
        "mcp__plans__*",
    ]
    if github_repo is not None:
        tools.append("mcp__github__*")

    mcp_servers: dict[str, McpServerConfig] = {
        "plans": plans_mcp_server(cwd),
    }
    if github_repo is not None:
        mcp_servers["github"] = _build_github_mcp_config()

    return ClaudeAgentOptions(
        tools=tools,
        # Belt-and-suspenders: even if `tools` is ever bypassed (issue #361),
        # writes stay denied. This set covers every write-capable SDK tool:
        #   - Write / Edit / MultiEdit — the core write trio. The SDK's own
        #     canonical write-intent regex at types.py:538 is
        #     "Write|MultiEdit|Edit", so MultiEdit is first-class.
        #   - NotebookEdit — Jupyter-targeting write tool.
        #   - TodoWrite — writes model-owned todo state; outside our
        #     read-only contract.
        #   - SlashCommand — meta-tool that could re-enter write paths via
        #     dispatched slash commands.
        disallowed_tools=[
            "Write",
            "Edit",
            "MultiEdit",
            "NotebookEdit",
            "TodoWrite",
            "SlashCommand",
        ],
        permission_mode="dontAsk",
        cwd=cwd,
        add_dirs=[memory_root],
        mcp_servers=mcp_servers,
        hooks={
            "PreToolUse": [
                HookMatcher(
                    matcher="Bash|Read|Glob|Grep",
                    # The SDK's HookCallback type alias demands a callable that
                    # accepts the *union* of every hook input type. Python is
                    # contravariant on parameters, so our narrower
                    # PreToolUse-only security_gate isn't a structural subtype
                    # even though it's correct here (this matcher only ever
                    # fires for PreToolUse events). Cast at the wire point.
                    hooks=[cast(HookCallback, security_gate)],
                    timeout=None,
                ),
            ],
        },
        system_prompt=system_prompt,
        model=model,
    )
