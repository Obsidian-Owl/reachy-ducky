"""Basic Memory MCP server configuration helper.

Produces the config dict for the external Basic Memory MCP server (installed
via ``uvx basic-memory``). The daemon passes this into
:class:`ClaudeAgentOptions.mcp_servers` at the key ``"basic-memory"`` when
it wants the brain to have MCP-level read/write access to the memory tree
(as opposed to the raw filesystem access the built-in ``Read``/``Glob``
tools already provide).

The server is spawned as an external process. Callers must ensure ``uvx``
is on PATH (it ships with uv).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def basic_memory_mcp_config(memory_root: Path) -> dict[str, Any]:
    """Build the MCP config dict for Basic Memory scoped to ``memory_root``.

    Args:
        memory_root: Absolute path to the Reachy Ducky memory tree
            (e.g., ``~/.reachy-ducky/memory/``). Must exist before the server
            starts — use :func:`ensure_layout` during daemon startup.

    Returns:
        A dict with a single ``mcpServers`` entry keyed on
        ``"basic-memory"``, suitable for merging into
        :class:`ClaudeAgentOptions.mcp_servers` or writing to ``.mcp.json``.
    """
    return {
        "mcpServers": {
            "basic-memory": {
                "command": "uvx",
                "args": [
                    "basic-memory",
                    "mcp",
                    "--project-path",
                    str(memory_root),
                ],
            }
        }
    }
