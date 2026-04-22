"""Unit tests for :mod:`reachy_ducky_daemon.memory.mcp_config`."""

from __future__ import annotations

from pathlib import Path

from reachy_ducky_daemon.memory.mcp_config import basic_memory_mcp_config


def test_config_points_at_memory_root(tmp_path: Path) -> None:
    """The config spawns `uvx basic-memory` scoped to the given memory_root."""
    cfg = basic_memory_mcp_config(memory_root=tmp_path)
    server = cfg["mcpServers"]["basic-memory"]
    assert server["command"] == "uvx"
    assert str(tmp_path) in " ".join(server["args"])


def test_config_args_include_basic_memory_mcp(tmp_path: Path) -> None:
    """Args invoke the `basic-memory mcp` subcommand (not just the CLI root)."""
    cfg = basic_memory_mcp_config(memory_root=tmp_path)
    args = cfg["mcpServers"]["basic-memory"]["args"]
    assert args[0] == "basic-memory"
    assert args[1] == "mcp"
    assert "--project-path" in args
