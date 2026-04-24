"""In-process plans MCP server for the thinking brain.

Exposes two read-only tools over ``create_sdk_mcp_server``:

* ``find_plans`` — list project-relative paths under a fixed set of
  conventional plan/spec locations (``docs/plans/**/*.md``, ``specs/**/*.md``,
  root ``AGENTS.md`` / ``CLAUDE.md`` / ``SPEC.md``, ``*.plan.md``).
* ``read_plan`` — read a single plan/spec file by its project-relative path,
  with two non-negotiable security properties:

    1. the resolved target path must stay inside the project root (``..``
       traversal and absolute paths are rejected by ``_read_plan``'s own
       ``is_relative_to`` check; symlink escapes are denied in
       :func:`_discover`, which silently drops any hit whose ``.resolve()``
       escapes ``project_root`` at discovery time);
    2. the target path must be one of the files that :func:`_discover` would
       advertise (``read_plan`` is not a general-purpose file reader — daemon
       source and secrets are denied even if they exist). Because
       ``_discover`` has already filtered symlink escapes, this membership
       check inherits the property: ``_read_plan`` can only return content
       for paths ``_discover`` already cleared.

The tool surface is intentionally tiny: Claude uses its built-in ``Read`` /
``Glob`` / ``Grep`` tools (gated by the PreToolUse security hook) for
everything else. ``find_plans`` / ``read_plan`` exist so Claude can discover
and orient without first having to remember the conventional locations.

The project root is **bound at server construction time**, not passed as a
tool argument. MCP tool args are NOT gated by the PreToolUse hook (which
covers only ``Bash``/``Read``/``Glob``/``Grep``); exposing ``project_root``
as an LLM-controlled argument would let a prompt-injected brain call
``find_plans(project_root="/")`` and enumerate AGENTS.md/CLAUDE.md/SPEC.md
anywhere readable. Closing over the resolved root inside the tool factory
makes that forgery impossible — the parameter simply does not exist on the
tool's input schema.

Single source of truth. Both layers call :func:`_discover` — ``list_plans``
returns its output as relative POSIX strings, and ``_read_plan`` validates by
checking membership in the same set. This collapses what used to be two
matchers (``Path.glob`` for discovery, ``PurePath.match`` for validation) into
one, which matters because those two engines are NOT parity-equivalent:

* ``PurePath.match("docs/plans/**/*.md")`` rejects ``docs/plans/a/b/c/d.md``
  that ``Path.glob("docs/plans/**/*.md")`` finds — under-approximation, making
  ``_read_plan`` deny paths ``list_plans`` advertised.
* ``PurePath.match("AGENTS.md")`` is a **tail match** (no leading anchor), so
  ``nested/AGENTS.md`` matches even though ``base.glob("AGENTS.md")`` only
  matches at the root — over-approximation, making attacker-droppable files
  readable even though ``list_plans`` wouldn't advertise them. That was the
  security widening we are eliminating here.

One ``_discover`` call per ``_read_plan`` is slightly more expensive than a
pattern check, but correct, auditable, and cache-able later if it matters.

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

from pathlib import Path
from typing import Any

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig

__all__ = [
    "list_plans",
    "plans_mcp_server",
]

# Conventional plan/spec locations. This set is the read surface of
# ``read_plan``; widening it widens what the brain can read via this server.
#
# ``Path.glob`` is the canonical matcher: both ``list_plans`` (discovery) and
# ``_read_plan`` (validation) resolve paths through :func:`_discover`, which
# uses this pattern list. ``fnmatch`` / ``PurePath.match`` are deliberately NOT
# used: the former has no recursive ``**`` operator and the latter is a tail
# match without a leading anchor (``nested/AGENTS.md`` would wrongly match
# ``AGENTS.md``) — both would drift from ``Path.glob`` semantics.
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

    The single source of truth for both :func:`list_plans` (discovery) and
    :func:`_read_plan` (validation). ``Path.glob`` handles ``**`` correctly
    (including the zero-intermediate-dir case) and anchors each pattern to
    ``base`` — i.e. ``base.glob("AGENTS.md")`` matches only ``base/AGENTS.md``,
    not ``base/nested/AGENTS.md``. That anchoring is what makes the read
    surface exactly as wide as the discover surface.

    Symlink-escape filter: a hit whose ``.resolve()`` lands outside ``base`` is
    silently dropped. The plans MCP tool surface — :func:`list_plans`
    (discovery) and :func:`_read_plan` (read with membership check) — both
    read from this set, so neither can return or accept an escape-resolving
    path. Closed via #8.
    """
    results: set[Path] = set()
    for pattern in _CONVENTIONAL_PATTERNS:
        for hit in base.glob(pattern):
            if not hit.is_file():
                continue
            resolved = hit.resolve()
            # Reject symlinks whose resolved target escapes ``base``.
            # ``is_relative_to`` (Python 3.9+) is the non-raising form
            # of the same check ``_read_plan`` uses. Filtering here
            # means both ``list_plans`` (discovery) and ``_read_plan``
            # (membership check) inherit the property for free — no
            # escaping path can be advertised, read, or leaked via
            # diagnostic. (#8)
            if not resolved.is_relative_to(base):
                continue
            results.add(resolved)
    return results


def list_plans(project_root: Path) -> list[str]:
    """Return sorted, deduplicated project-relative paths to conventional plans.

    Pure helper, testable without the SDK. ``set``-based dedup handles the
    case where a single file matches multiple patterns
    (e.g. ``docs/plans/x.plan.md`` matches both ``docs/plans/**/*.md`` and
    ``*.plan.md``).
    """
    base = project_root.resolve()
    return sorted(p.relative_to(base).as_posix() for p in _discover(base))


def _read_plan(project_root: Path, rel_path: str) -> str:
    """Return the UTF-8 text of a plan/spec file, or raise.

    Raises:
        PermissionError: if ``rel_path`` resolves outside ``project_root``
            (``..`` escape, absolute path, symlink escape), or if the
            resolved path is not one of the files that :func:`_discover`
            would advertise. The membership check fires regardless of
            whether the path exists on disk, so probing non-plan locations
            cannot be used as an existence oracle for arbitrary files.
        OSError: subclasses (``FileNotFoundError``, ``PermissionError`` from
            the filesystem, etc.) surface from ``read_text``.
        UnicodeDecodeError: if the file exists and is advertised but isn't
            valid UTF-8.
        ValueError: if ``rel_path`` contains null bytes or similarly rejected
            characters that ``Path.resolve()`` refuses.
    """
    base = project_root.resolve()
    target = (base / rel_path).resolve()

    try:
        target.relative_to(base)
    except ValueError as exc:
        raise PermissionError(f"path escapes project root: {rel_path}") from exc

    if target not in _discover(base):
        # Single source of truth: the target must be something list_plans
        # would advertise. ``_discover`` only returns files that exist, so a
        # missing file here falls into this branch rather than a separate
        # FileNotFoundError check.
        raise PermissionError(f"not a plan or spec file: {rel_path}")

    return target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# @tool wrappers — built per-server so the project root is closed over.
# ---------------------------------------------------------------------------


def _make_plans_tools(project_root: Path) -> tuple[SdkMcpTool[Any], SdkMcpTool[Any]]:
    """Build ``(find_plans, read_plan)`` ``SdkMcpTool`` instances bound to ``project_root``.

    The resolved root is captured in the closure of each handler; there is no
    ``project_root`` parameter on either tool's input schema, so an LLM cannot
    redirect the tools at a different directory by forging arguments.

    Module-private (leading underscore) because the public API is
    :func:`plans_mcp_server`; the test suite reaches in here to invoke the
    tool handlers directly, which is fine because the tests and the module
    ship in the same package.
    """
    resolved_root = project_root.resolve()

    @tool(
        "find_plans",
        "List plan/spec file paths under conventional locations "
        "(docs/plans/**/*.md, specs/**/*.md, root AGENTS.md / CLAUDE.md / "
        "SPEC.md, *.plan.md) in the project root this server is bound to.",
        {},
    )
    async def find_plans(_args: dict[str, Any]) -> dict[str, Any]:
        """MCP ``find_plans`` wrapper: format :func:`list_plans` as a text block.

        Catches ``OSError`` (filesystem errors during ``resolve()`` / ``glob``)
        and ``ValueError`` (null bytes, etc.) so the handler never raises — the
        SDK dispatcher can rely on a well-formed response.
        """
        try:
            paths = list_plans(resolved_root)
        except (OSError, ValueError) as exc:
            return {
                "content": [{"type": "text", "text": f"error: failed to list plans: {exc}"}],
                "isError": True,
            }
        text = "\n".join(paths) if paths else "(no plans found)"
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "read_plan",
        "Read a plan or spec file by its project-relative path in the project "
        "root this server is bound to. Rejects paths that escape the project "
        "root and paths that are not one of the conventional plan/spec "
        "locations.",
        {"rel_path": str},
    )
    async def read_plan(args: dict[str, Any]) -> dict[str, Any]:
        """MCP ``read_plan`` wrapper: call :func:`_read_plan` and surface errors
        as MCP ``isError`` responses rather than raising.

        The ``except`` clause is intentionally broad:

        * ``OSError`` is the superclass of ``PermissionError`` and
          ``FileNotFoundError`` (so both stay covered) plus other filesystem
          errors that used to escape the handler.
        * ``UnicodeDecodeError`` fires when the file exists and is advertised
          but isn't valid UTF-8 (e.g. a ``\\xff`` byte).
        * ``ValueError`` fires when ``Path.resolve()`` refuses the input — most
          commonly a null byte (``..\\x00/.env``) which used to crash the
          async handler uncaught.
        """
        rel_path = args.get("rel_path")
        if not isinstance(rel_path, str):
            return {
                "content": [{"type": "text", "text": "error: rel_path must be a string"}],
                "isError": True,
            }
        try:
            text = _read_plan(resolved_root, rel_path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return {
                "content": [{"type": "text", "text": f"error: failed to read plan: {exc}"}],
                "isError": True,
            }
        return {"content": [{"type": "text", "text": text}]}

    return find_plans, read_plan


def plans_mcp_server(project_root: Path) -> McpSdkServerConfig:
    """Build the in-process plans MCP server bound to ``project_root``.

    The returned server exposes ``find_plans`` (no args) and
    ``read_plan(rel_path)``. The root is bound at construction time; the LLM
    cannot forge a different root via tool args because there is no
    ``project_root`` parameter on either tool's input schema.

    Returns an :class:`McpSdkServerConfig` TypedDict that can be passed
    directly into ``ClaudeAgentOptions(mcp_servers={"plans": <config>})``.
    """
    find_plans, read_plan = _make_plans_tools(project_root)
    tools: list[SdkMcpTool[Any]] = [find_plans, read_plan]
    return create_sdk_mcp_server(name="plans", version="0.1.0", tools=tools)
