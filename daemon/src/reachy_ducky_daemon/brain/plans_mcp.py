"""In-process plans MCP server for the thinking brain.

Exposes two read-only tools over ``create_sdk_mcp_server``:

* :obj:`find_plans` — list project-relative paths under a fixed set of
  conventional plan/spec locations (``docs/plans/**/*.md``, ``specs/**/*.md``,
  root ``AGENTS.md`` / ``CLAUDE.md`` / ``SPEC.md``, ``*.plan.md``).
* :obj:`read_plan` — read a single plan/spec file by its project-relative path,
  with two non-negotiable security properties:

    1. the resolved target path must stay inside the project root
       (``..`` traversal, absolute paths, and symlink escapes are denied);
    2. the target path must match one of the conventional patterns
       (``read_plan`` is not a general-purpose file reader — daemon source and
       secrets are denied even if they exist).

The tool surface is intentionally tiny: Claude uses its built-in ``Read`` /
``Glob`` / ``Grep`` tools (gated by the PreToolUse security hook) for
everything else. ``find_plans`` / ``read_plan`` exist so Claude can discover
and orient without first having to remember the conventional locations.

Verified SDK shape (``claude_agent_sdk`` 0.1.60)::

    @tool(name: str,
          description: str,
          input_schema: type | dict[str, Any],
          annotations: ToolAnnotations | None = None)
    -> Callable[[handler], SdkMcpTool]

    create_sdk_mcp_server(
        name: str,
        version: str = "1.0.0",
        tools: list[SdkMcpTool[Any]] | None = None,
    ) -> McpSdkServerConfig  # TypedDict {type: "sdk", name, instance}

    handler: async def h(args: dict[str, Any]) -> dict[str, Any]
    return shape: {"content": [{"type": "text", "text": "..."}],
                   "isError": bool (optional)}

Not wired into :class:`ClaudeSDKBrain` here; Task 3.4 registers this server in
``build_brain_options(...)``.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig

__all__ = [
    "find_plans",
    "plans_mcp_server",
    "read_plan",
]

# Conventional plan/spec locations. This set is the read surface of
# ``read_plan``; widening it widens what the brain can read via this server.
#
# Matching uses ``pathlib.Path.glob`` for both discovery (``_list_plans``)
# and single-path validation (``_read_plan``) so both layers share identical
# semantics. ``fnmatch`` is deliberately NOT used: its ``*`` matches path
# separators and it has no recursive ``**`` operator, so ``docs/plans/**/*.md``
# under fnmatch would fail to match ``docs/plans/hello.md`` (zero intermediate
# directories) while ``Path.glob`` correctly matches both ``docs/plans/*.md``
# and ``docs/plans/a/b/c/*.md`` for the same pattern.
_CONVENTIONAL_PATTERNS: tuple[str, ...] = (
    "docs/plans/**/*.md",
    "specs/**/*.md",
    "AGENTS.md",
    "CLAUDE.md",
    "SPEC.md",
    "*.plan.md",
)


def _discover(base: Path) -> set[Path]:
    """Return the absolute paths of every file under ``base`` matched by any
    conventional pattern.

    Shared engine for discovery and validation: both layers call this helper
    so path-inclusion semantics are identical. ``Path.glob`` handles ``**``
    correctly (including the zero-intermediate-dir case); this avoids the
    fnmatch/Path.match divergence where ``docs/plans/**/*.md`` under those
    flat matchers would reject ``docs/plans/hello.md``.
    """
    results: set[Path] = set()
    for pattern in _CONVENTIONAL_PATTERNS:
        for hit in base.glob(pattern):
            if hit.is_file():
                # Resolve so symlink-based dedup and membership tests agree
                # with the resolved ``target`` computed in ``_read_plan``.
                results.add(hit.resolve())
    return results


def _list_plans(project_root: Path) -> list[str]:
    """Return sorted, deduplicated project-relative paths to conventional plans.

    Pure helper, testable without the SDK. ``set``-based dedup handles the
    case where a single file matches multiple patterns
    (e.g. ``docs/plans/x.plan.md`` matches both ``docs/plans/**/*.md`` and
    ``*.plan.md``).
    """
    base = project_root.resolve()
    return sorted(p.relative_to(base).as_posix() for p in _discover(base))


def _matches_conventional_pattern(rel_as_posix: str) -> bool:
    """Return ``True`` iff the project-relative path string matches any
    conventional plan/spec pattern, using :func:`Path.glob` semantics for
    ``**`` (zero-or-more intermediate directories).

    ``pathlib.PurePath.match`` on 3.12 treats ``X/**/Y`` as requiring at
    least one intermediate segment (so ``docs/plans/**/*.md`` would miss
    ``docs/plans/hello.md``), and ``fnmatch.fnmatch`` has no recursive
    ``**`` operator at all. We therefore test each pattern against the
    candidate AND against a ``**/`` -collapsed variant so zero-depth hits
    (e.g. ``docs/plans/x.md``) and deep hits (e.g. ``docs/plans/a/b.md``)
    both pass, matching the exact set discovered by :func:`Path.glob`.
    """
    candidate = PurePosixPath(rel_as_posix)
    for pattern in _CONVENTIONAL_PATTERNS:
        if candidate.match(pattern):
            return True
        if "**/" in pattern and candidate.match(pattern.replace("**/", "")):
            return True
    return False


def _read_plan(project_root: Path, rel_path: str) -> str:
    """Return the UTF-8 text of a plan/spec file, or raise.

    Raises:
        PermissionError: if ``rel_path`` resolves outside ``project_root``
            (``..`` escape, absolute path, symlink escape), or if the
            resolved path is not one of the conventional plan/spec
            locations. The pattern check fires regardless of whether the
            path exists on disk, so probing non-plan locations cannot be
            used as an existence oracle for arbitrary files.
        FileNotFoundError: if ``rel_path`` would be a legitimate plan
            location (e.g. ``docs/plans/foo.md``) but no file is there.
    """
    base = project_root.resolve()
    target = (base / rel_path).resolve()

    try:
        target.relative_to(base)
    except ValueError as exc:
        raise PermissionError(f"path escapes project root: {rel_path}") from exc

    rel_as_posix = target.relative_to(base).as_posix()
    if not _matches_conventional_pattern(rel_as_posix):
        raise PermissionError(f"not a plan or spec file: {rel_path}")

    if not target.is_file():
        raise FileNotFoundError(rel_path)

    return target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# @tool wrappers — thin shells over the pure helpers.
# ---------------------------------------------------------------------------


@tool(
    "find_plans",
    "List plan/spec file paths under conventional locations "
    "(docs/plans/**/*.md, specs/**/*.md, root AGENTS.md / CLAUDE.md / "
    "SPEC.md, *.plan.md) in the given project root.",
    {"project_root": str},
)
async def find_plans(args: dict[str, Any]) -> dict[str, Any]:
    """MCP ``find_plans`` wrapper: format :func:`_list_plans` as a text block."""
    project_root = args["project_root"]
    if not isinstance(project_root, str):
        return {
            "content": [
                {"type": "text", "text": "error: project_root must be a string"},
            ],
            "isError": True,
        }
    paths = _list_plans(Path(project_root))
    text = "\n".join(paths) if paths else "(no plans found)"
    return {"content": [{"type": "text", "text": text}]}


@tool(
    "read_plan",
    "Read a plan or spec file by its project-relative path. "
    "Rejects paths that escape the project root and paths that are not one "
    "of the conventional plan/spec locations.",
    {"project_root": str, "rel_path": str},
)
async def read_plan(args: dict[str, Any]) -> dict[str, Any]:
    """MCP ``read_plan`` wrapper: call :func:`_read_plan` and surface errors
    as MCP ``isError`` responses rather than raising."""
    project_root = args["project_root"]
    rel_path = args["rel_path"]
    if not isinstance(project_root, str) or not isinstance(rel_path, str):
        return {
            "content": [
                {"type": "text", "text": "error: project_root and rel_path must be strings"},
            ],
            "isError": True,
        }
    try:
        text = _read_plan(Path(project_root), rel_path)
    except (PermissionError, FileNotFoundError) as exc:
        return {
            "content": [{"type": "text", "text": f"error: {exc}"}],
            "isError": True,
        }
    return {"content": [{"type": "text", "text": text}]}


def plans_mcp_server() -> McpSdkServerConfig:
    """Build the in-process plans MCP server.

    Returns an :class:`McpSdkServerConfig` TypedDict that can be passed
    directly into ``ClaudeAgentOptions(mcp_servers={"plans": <config>})``.
    """
    tools: list[SdkMcpTool[Any]] = [find_plans, read_plan]
    return create_sdk_mcp_server(name="plans", version="0.1.0", tools=tools)
