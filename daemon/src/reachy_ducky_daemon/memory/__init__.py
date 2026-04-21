"""Reachy Ducky memory subpackage — on-disk Markdown layout scaffolder."""

from __future__ import annotations

from .layout import ensure_layout, ensure_project
from .mcp_config import basic_memory_mcp_config

__all__ = ["basic_memory_mcp_config", "ensure_layout", "ensure_project"]
