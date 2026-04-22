"""Unit tests for the brain options factory.

All tests here are pure config-shape assertions against the
:class:`ClaudeAgentOptions` returned by :func:`build_brain_options`. No live
Claude, no subprocess spawn, no filesystem or network I/O at call time.

The factory assembles the previously-built components (``security_gate``,
``plans_mcp_server``) plus optional ``github-mcp-server`` config into a
single options object that :class:`ClaudeSDKBrain` will consume in Task 3.5.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from claude_agent_sdk import ClaudeAgentOptions, HookMatcher
from reachy_ducky_daemon.brain.options import (
    DEFAULT_BRAIN_SYSTEM_PROMPT,
    build_brain_options,
)
from reachy_ducky_daemon.brain.security_gate import security_gate


def _mcp_dict(opts: ClaudeAgentOptions) -> dict[str, Any]:
    """Narrow ``opts.mcp_servers`` to a concrete ``dict``.

    The SDK types ``mcp_servers`` as ``dict | str | Path`` (the latter two for
    loading from a file); the factory always returns the dict shape. Asserting
    this at the boundary lets downstream assertions index freely without
    fighting the union type.
    """
    servers = opts.mcp_servers
    assert isinstance(servers, dict)
    return dict(servers)


# ---------------------------------------------------------------------------
# Default call (no github_repo)
# ---------------------------------------------------------------------------


def test_default_returns_claude_agent_options(tmp_path: Path) -> None:
    """Factory returns a ``ClaudeAgentOptions`` instance."""
    opts = build_brain_options(cwd=tmp_path, memory_root=tmp_path / "mem")
    assert isinstance(opts, ClaudeAgentOptions)


def test_default_tools_allowlist(tmp_path: Path) -> None:
    """Without ``github_repo``, ``tools`` is the locked-down read-only set.

    Per SDK issue #361, ``tools=[...]`` is the real toolset restrictor — not
    ``allowed_tools``. The set below is what the brain will actually be able
    to invoke.
    """
    opts = build_brain_options(cwd=tmp_path, memory_root=tmp_path / "mem")
    assert set(opts.tools or []) == {
        "Read",
        "Glob",
        "Grep",
        "Bash",
        "Task",
        "mcp__plans__*",
    }
    assert "mcp__github__*" not in (opts.tools or [])


def test_default_disallowed_tools_blocks_writes(tmp_path: Path) -> None:
    """``disallowed_tools`` hard-denies every write-capable SDK tool."""
    opts = build_brain_options(cwd=tmp_path, memory_root=tmp_path / "mem")
    for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit", "TodoWrite", "SlashCommand"):
        assert tool in opts.disallowed_tools


def test_disallowed_tools_pins_expected_set(tmp_path: Path) -> None:
    """disallowed_tools is the belt-and-suspenders bucket; changes must be deliberate."""
    opts = build_brain_options(cwd=tmp_path, memory_root=tmp_path / "mem")
    assert set(opts.disallowed_tools) == {
        "Write",
        "Edit",
        "MultiEdit",
        "NotebookEdit",
        "TodoWrite",
        "SlashCommand",
    }


def test_permission_mode_is_dont_ask(tmp_path: Path) -> None:
    """``permission_mode`` is the locked-down ``"dontAsk"`` string per research."""
    opts = build_brain_options(cwd=tmp_path, memory_root=tmp_path / "mem")
    assert opts.permission_mode == "dontAsk"


def test_default_mcp_servers_only_has_plans(tmp_path: Path) -> None:
    """Without ``github_repo``, only the in-process ``plans`` MCP is wired."""
    opts = build_brain_options(cwd=tmp_path, memory_root=tmp_path / "mem")
    servers = _mcp_dict(opts)
    assert set(servers.keys()) == {"plans"}


def test_default_plans_mcp_is_sdk_server(tmp_path: Path) -> None:
    """``mcp_servers['plans']`` is the in-process SDK server config shape."""
    opts = build_brain_options(cwd=tmp_path, memory_root=tmp_path / "mem")
    plans_cfg = _mcp_dict(opts)["plans"]
    assert isinstance(plans_cfg, dict)
    assert plans_cfg["type"] == "sdk"
    assert plans_cfg["name"] == "plans"
    assert plans_cfg["instance"] is not None


def test_default_hooks_wire_security_gate(tmp_path: Path) -> None:
    """PreToolUse hook list contains the configured ``security_gate`` matcher."""
    opts = build_brain_options(cwd=tmp_path, memory_root=tmp_path / "mem")
    assert opts.hooks is not None
    pre_tool_use = opts.hooks["PreToolUse"]
    assert len(pre_tool_use) == 1
    matcher = pre_tool_use[0]
    assert isinstance(matcher, HookMatcher)
    assert matcher.matcher == "Bash|Read|Glob|Grep"
    assert len(matcher.hooks) == 1
    # Identity check: the exact function we imported is what's wired in.
    assert matcher.hooks[0] is security_gate


def test_cwd_and_add_dirs_set(tmp_path: Path) -> None:
    """``cwd`` is the project root; ``add_dirs`` adds the memory tree."""
    memory = tmp_path / "mem"
    opts = build_brain_options(cwd=tmp_path, memory_root=memory)
    assert opts.cwd == tmp_path
    assert memory in opts.add_dirs


def test_default_system_prompt_is_brain_default(tmp_path: Path) -> None:
    """Without override, the system_prompt is ``DEFAULT_BRAIN_SYSTEM_PROMPT``."""
    opts = build_brain_options(cwd=tmp_path, memory_root=tmp_path / "mem")
    assert opts.system_prompt == DEFAULT_BRAIN_SYSTEM_PROMPT


def test_default_model_set(tmp_path: Path) -> None:
    """Default ``model`` is ``claude-sonnet-4-6`` (matches existing ClaudeSDKBrain default)."""
    opts = build_brain_options(cwd=tmp_path, memory_root=tmp_path / "mem")
    assert opts.model == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# With github_repo
# ---------------------------------------------------------------------------


def test_github_repo_adds_mcp_github_glob_to_tools(tmp_path: Path) -> None:
    """Passing ``github_repo`` adds ``mcp__github__*`` to the tools allowlist."""
    opts = build_brain_options(
        cwd=tmp_path,
        memory_root=tmp_path / "mem",
        github_repo="owner/repo",
    )
    assert "mcp__github__*" in (opts.tools or [])


def test_github_repo_adds_github_to_mcp_servers(tmp_path: Path) -> None:
    """Passing ``github_repo`` registers the ``github`` MCP server."""
    opts = build_brain_options(
        cwd=tmp_path,
        memory_root=tmp_path / "mem",
        github_repo="owner/repo",
    )
    assert set(_mcp_dict(opts).keys()) == {"plans", "github"}


def test_github_mcp_config_is_stdio_npx_spawn(tmp_path: Path) -> None:
    """``github`` MCP is configured as a stdio npx spawn with expected args."""
    opts = build_brain_options(
        cwd=tmp_path,
        memory_root=tmp_path / "mem",
        github_repo="owner/repo",
    )
    github_cfg: Any = _mcp_dict(opts)["github"]
    assert isinstance(github_cfg, dict)
    assert github_cfg["command"] == "npx"
    args = github_cfg["args"]
    assert args[:2] == ["-y", "github-mcp-server"]
    assert "--read-only" in args


def test_github_mcp_toolsets_are_read_only_set(tmp_path: Path) -> None:
    """``--toolsets`` restricts github-mcp-server to read-only surfaces."""
    opts = build_brain_options(
        cwd=tmp_path,
        memory_root=tmp_path / "mem",
        github_repo="owner/repo",
    )
    github_cfg: Any = _mcp_dict(opts)["github"]
    args = github_cfg["args"]
    idx = args.index("--toolsets")
    toolsets_value = args[idx + 1]
    # All four toolsets must be present; order is not significant.
    assert set(toolsets_value.split(",")) == {
        "pull_requests",
        "issues",
        "actions",
        "repos",
    }


def test_github_mcp_passes_env_token_from_process(tmp_path: Path) -> None:
    """``env`` carries ``GITHUB_PERSONAL_ACCESS_TOKEN`` from the process env.

    The SDK's ``McpStdioServerConfig.env`` field is a plain ``dict[str, str]``
    with no placeholder expansion. The factory must read the process env at
    call time and pass the literal token value through so the spawned server
    can authenticate.
    """
    fake_token = "ghp_test_token_value"
    with patch.dict(os.environ, {"GITHUB_PERSONAL_ACCESS_TOKEN": fake_token}, clear=False):
        opts = build_brain_options(
            cwd=tmp_path,
            memory_root=tmp_path / "mem",
            github_repo="owner/repo",
        )
    github_cfg: Any = _mcp_dict(opts)["github"]
    assert github_cfg["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == fake_token


def test_github_mcp_env_empty_when_token_missing(tmp_path: Path) -> None:
    """When ``GITHUB_PERSONAL_ACCESS_TOKEN`` isn't set, ``env`` has an empty string.

    We don't raise here — the factory is a config builder, not a secrets
    validator. An empty token will surface as an auth failure when the brain
    actually dispatches a tool, which is the right layer for that error.
    """
    env_no_token = {k: v for k, v in os.environ.items() if k != "GITHUB_PERSONAL_ACCESS_TOKEN"}
    with patch.dict(os.environ, env_no_token, clear=True):
        opts = build_brain_options(
            cwd=tmp_path,
            memory_root=tmp_path / "mem",
            github_repo="owner/repo",
        )
    github_cfg: Any = _mcp_dict(opts)["github"]
    # Either an empty string or the key simply present but empty — assert by value.
    assert github_cfg["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == ""


def test_github_repo_empty_string_rejected(tmp_path: Path) -> None:
    """Empty ``github_repo`` is rejected with ValueError; symmetric with Path guards."""
    with pytest.raises(ValueError, match="owner/repo"):
        build_brain_options(
            cwd=tmp_path,
            memory_root=tmp_path / "mem",
            github_repo="",
        )


def test_github_repo_missing_slash_rejected(tmp_path: Path) -> None:
    """``github_repo`` without a ``/`` separator is rejected."""
    with pytest.raises(ValueError, match="owner/repo"):
        build_brain_options(
            cwd=tmp_path,
            memory_root=tmp_path / "mem",
            github_repo="foo",
        )


def test_github_repo_trailing_slash_rejected(tmp_path: Path) -> None:
    """``github_repo`` with an empty repo component is rejected."""
    with pytest.raises(ValueError, match="owner/repo"):
        build_brain_options(
            cwd=tmp_path,
            memory_root=tmp_path / "mem",
            github_repo="owner/",
        )


def test_github_repo_valid_owner_repo_accepted(tmp_path: Path) -> None:
    """A well-formed ``owner/repo`` string builds the github MCP config."""
    opts = build_brain_options(
        cwd=tmp_path,
        memory_root=tmp_path / "mem",
        github_repo="Obsidian-Owl/reachy-ducky",
    )
    assert "github" in _mcp_dict(opts)


# ---------------------------------------------------------------------------
# Edge cases / overrides
# ---------------------------------------------------------------------------


def test_memory_root_need_not_exist_at_call_time(tmp_path: Path) -> None:
    """The factory does not touch the filesystem, so ``memory_root`` need not exist.

    This is required so the daemon can build options before the memory tree
    is materialised (initial daemon start, fresh Mac user, etc.).
    """
    missing_memory = tmp_path / "does-not-exist" / "mem"
    assert not missing_memory.exists()
    opts = build_brain_options(cwd=tmp_path, memory_root=missing_memory)
    assert missing_memory in opts.add_dirs


def test_custom_system_prompt_passes_through(tmp_path: Path) -> None:
    """Caller-provided ``system_prompt`` is used instead of the default."""
    custom = "You are a highly specialized test observer."
    opts = build_brain_options(
        cwd=tmp_path,
        memory_root=tmp_path / "mem",
        system_prompt=custom,
    )
    assert opts.system_prompt == custom
    assert opts.system_prompt != DEFAULT_BRAIN_SYSTEM_PROMPT


def test_custom_model_passes_through(tmp_path: Path) -> None:
    """Caller-provided ``model`` is used instead of the default."""
    opts = build_brain_options(
        cwd=tmp_path,
        memory_root=tmp_path / "mem",
        model="claude-opus-4-7",
    )
    assert opts.model == "claude-opus-4-7"


def test_cwd_must_be_path_not_str(tmp_path: Path) -> None:
    """The factory enforces ``Path`` for ``cwd`` (and ``memory_root``).

    Per python-standards.md, we pass ``Path`` objects between subpackages;
    accepting a bare ``str`` silently would let type-confused callers through.
    """
    with pytest.raises(TypeError):
        build_brain_options(
            cwd=str(tmp_path),  # type: ignore[arg-type]
            memory_root=tmp_path / "mem",
        )


def test_memory_root_must_be_path_not_str(tmp_path: Path) -> None:
    """Symmetric enforcement: ``memory_root`` must also be ``Path``."""
    with pytest.raises(TypeError):
        build_brain_options(
            cwd=tmp_path,
            memory_root=str(tmp_path / "mem"),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("repo_value", "expect_github_key"),
    [
        pytest.param(None, False, id="None disables github-mcp"),
        pytest.param("owner/repo", True, id="string enables github-mcp"),
    ],
)
def test_github_repo_toggles_mcp_entry(
    tmp_path: Path,
    repo_value: str | None,
    expect_github_key: bool,
) -> None:
    """``github_repo=None`` omits the github entry; any non-None string enables it."""
    opts = build_brain_options(
        cwd=tmp_path,
        memory_root=tmp_path / "mem",
        github_repo=repo_value,
    )
    assert ("github" in _mcp_dict(opts)) is expect_github_key


def test_default_brain_system_prompt_is_non_empty_string() -> None:
    """``DEFAULT_BRAIN_SYSTEM_PROMPT`` is a non-empty module-level constant."""
    assert isinstance(DEFAULT_BRAIN_SYSTEM_PROMPT, str)
    assert DEFAULT_BRAIN_SYSTEM_PROMPT.strip() != ""
