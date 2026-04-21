"""Unit tests for :meth:`ClaudeSDKBrain.with_tools` — Pattern B integration.

These tests patch :func:`sdk_query` so no live Claude call is made; we inspect
the ``options`` kwarg the brain forwards to the SDK. The goal is to prove
:meth:`with_tools` wires the factory output through unchanged, while the
existing no-arg constructor path (Task 2.2) continues to produce the minimal
text-stream options shape.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from reachy_ducky_daemon.brain.claude_sdk import ClaudeSDKBrain
from reachy_ducky_protocol.messages import BrainRequest

# ---------------------------------------------------------------------------
# with_tools(...) — new Pattern B construction path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_tools_passes_configured_options_to_sdk_query(tmp_path: Path) -> None:
    """``with_tools`` produces an instance whose ``query`` forwards the pre-built options."""
    seen: dict[str, Any] = {}

    async def fake_query(prompt: str, options: Any) -> AsyncIterator[dict[str, Any]]:
        seen["options"] = options
        seen["prompt"] = prompt
        yield {"type": "text", "text": "ok"}

    with patch("reachy_ducky_daemon.brain.claude_sdk.sdk_query", new=fake_query):
        brain = ClaudeSDKBrain.with_tools(cwd=tmp_path, memory_root=tmp_path / "mem")
        resp = await brain.query(BrainRequest(user_utterance="hi"))

    opts = seen["options"]
    assert seen["prompt"] == "hi"
    assert resp.text == "ok"
    # Options are those ``build_brain_options`` produces.
    assert opts.permission_mode == "dontAsk"
    assert "Read" in (opts.tools or [])
    assert "mcp__plans__*" in (opts.tools or [])
    # No ``github_repo`` → no github tool / server.
    assert "mcp__github__*" not in (opts.tools or [])
    assert isinstance(opts.mcp_servers, dict)
    assert "plans" in opts.mcp_servers
    assert "github" not in opts.mcp_servers
    # Security gate wired via PreToolUse hook.
    assert opts.hooks is not None
    assert "PreToolUse" in opts.hooks


@pytest.mark.asyncio
async def test_with_tools_with_github_repo_includes_github_tools(tmp_path: Path) -> None:
    """Passing ``github_repo`` wires the github-mcp-server + allows its tool glob."""
    seen: dict[str, Any] = {}

    async def fake_query(prompt: str, options: Any) -> AsyncIterator[dict[str, Any]]:  # noqa: ARG001
        seen["options"] = options
        yield {"type": "text", "text": ""}

    with patch("reachy_ducky_daemon.brain.claude_sdk.sdk_query", new=fake_query):
        brain = ClaudeSDKBrain.with_tools(
            cwd=tmp_path,
            memory_root=tmp_path / "mem",
            github_repo="Obsidian-Owl/reachy-ducky",
        )
        await brain.query(BrainRequest(user_utterance="check the PRs"))

    opts = seen["options"]
    assert "mcp__github__*" in (opts.tools or [])
    assert isinstance(opts.mcp_servers, dict)
    assert "github" in opts.mcp_servers


@pytest.mark.asyncio
async def test_classic_constructor_still_works(tmp_path: Path) -> None:
    """Backward compat: ``ClaudeSDKBrain()`` keeps the minimal text-stream shape.

    The options the brain hands to :func:`sdk_query` must have no tool surface,
    no MCP servers, and no hooks — exactly what Task 2.2 produced.
    """
    _ = tmp_path  # keep the pytest fixture pattern consistent with the other tests
    seen: dict[str, Any] = {}

    async def fake_query(prompt: str, options: Any) -> AsyncIterator[dict[str, Any]]:  # noqa: ARG001
        seen["options"] = options
        yield {"type": "text", "text": "classic ok"}

    with patch("reachy_ducky_daemon.brain.claude_sdk.sdk_query", new=fake_query):
        brain = ClaudeSDKBrain()
        resp = await brain.query(BrainRequest(user_utterance="hi"))

    assert resp.text == "classic ok"
    opts = seen["options"]
    # Minimal text-stream shape: system_prompt + model only.
    assert opts.tools is None
    assert opts.mcp_servers == {}
    assert opts.hooks is None
    assert opts.permission_mode is None
    assert opts.disallowed_tools == []
    assert opts.model == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_with_tools_passes_custom_system_prompt(tmp_path: Path) -> None:
    """Caller-provided ``system_prompt`` flows through ``with_tools``."""
    custom = "You are a specialized test observer."
    seen: dict[str, Any] = {}

    async def fake_query(prompt: str, options: Any) -> AsyncIterator[dict[str, Any]]:  # noqa: ARG001
        seen["options"] = options
        yield {"type": "text", "text": ""}

    with patch("reachy_ducky_daemon.brain.claude_sdk.sdk_query", new=fake_query):
        brain = ClaudeSDKBrain.with_tools(
            cwd=tmp_path,
            memory_root=tmp_path / "mem",
            system_prompt=custom,
        )
        await brain.query(BrainRequest(user_utterance="hi"))

    assert seen["options"].system_prompt == custom


@pytest.mark.asyncio
async def test_with_tools_passes_custom_model(tmp_path: Path) -> None:
    """Caller-provided ``model`` flows through ``with_tools``."""
    seen: dict[str, Any] = {}

    async def fake_query(prompt: str, options: Any) -> AsyncIterator[dict[str, Any]]:  # noqa: ARG001
        seen["options"] = options
        yield {"type": "text", "text": ""}

    with patch("reachy_ducky_daemon.brain.claude_sdk.sdk_query", new=fake_query):
        brain = ClaudeSDKBrain.with_tools(
            cwd=tmp_path,
            memory_root=tmp_path / "mem",
            model="claude-opus-4-7",
        )
        await brain.query(BrainRequest(user_utterance="hi"))

    assert seen["options"].model == "claude-opus-4-7"
