"""Unit tests for the in-process plans MCP server.

Exercises the pure helpers (``list_plans``, ``_read_plan``) directly so the
security properties are locked in without going through the SDK layer, plus a
hello-world integration assertion that the @tool-decorated wrappers are wired
through ``create_sdk_mcp_server`` with the right names/descriptions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from reachy_ducky_daemon.brain.plans_mcp import (
    _CONVENTIONAL_PATTERNS,
    _make_plans_tools,
    _read_plan,
    list_plans,
    plans_mcp_server,
)


def _require_dict_schema(schema: Any, *, tool_name: str) -> dict[str, Any]:
    """Narrow a tool's ``input_schema`` to the dict case or fail fast.

    The SDK types ``input_schema`` as ``type | dict[str, Any]``. Our plans
    tools construct with dicts, but relying on ``assert isinstance`` to
    narrow is brittle under ``python -O`` and leaks a confusing ``TypeError``
    on ``"x" in schema`` if the SDK ever returns the class variant. Use
    ``pytest.fail`` so the failure mode is a clear, optimization-independent
    test failure.
    """
    if not isinstance(schema, dict):
        pytest.fail(f"{tool_name} input_schema is {type(schema).__name__}, expected dict")
    return schema


# ---------------------------------------------------------------------------
# list_plans
# ---------------------------------------------------------------------------


def test_list_plans_empty_project(tmp_path: Path) -> None:
    """An empty project tree returns an empty list."""
    assert list_plans(tmp_path) == []


def test_list_plans_single_docs_plan(tmp_path: Path) -> None:
    """A plan under docs/plans/ is discovered and returned relative to root."""
    plan = tmp_path / "docs" / "plans" / "foo.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# foo")

    assert list_plans(tmp_path) == ["docs/plans/foo.md"]


def test_list_plans_nested_docs_plans(tmp_path: Path) -> None:
    """Recursive ``**`` glob picks up plans in subdirectories under docs/plans/."""
    (tmp_path / "docs" / "plans" / "sub").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "sub" / "deep.md").write_text("deep")

    assert list_plans(tmp_path) == ["docs/plans/sub/deep.md"]


def test_list_plans_multiple_locations_sorted_dedup(tmp_path: Path) -> None:
    """Plans from every conventional location are merged, sorted, and deduplicated."""
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "specs").mkdir()

    (tmp_path / "docs" / "plans" / "alpha.md").write_text("alpha")
    (tmp_path / "docs" / "plans" / "zulu.md").write_text("zulu")
    (tmp_path / "specs" / "mike.md").write_text("mike")
    (tmp_path / "CLAUDE.md").write_text("root claude")
    (tmp_path / "AGENTS.md").write_text("root agents")
    (tmp_path / "SPEC.md").write_text("root spec")
    (tmp_path / "bravo.plan.md").write_text("bravo plan")

    result = list_plans(tmp_path)

    assert result == sorted(result), "results must be sorted lexicographically"
    assert len(result) == len(set(result)), "results must be deduplicated"
    assert result == [
        "AGENTS.md",
        "CLAUDE.md",
        "SPEC.md",
        "bravo.plan.md",
        "docs/plans/alpha.md",
        "docs/plans/zulu.md",
        "specs/mike.md",
    ]


def test_list_plans_readme_excluded(tmp_path: Path) -> None:
    """README.md at the root is not a conventional plan and is not returned."""
    (tmp_path / "README.md").write_text("readme")

    assert list_plans(tmp_path) == []


def test_list_plans_daemon_src_excluded(tmp_path: Path) -> None:
    """Markdown under daemon/src/ (not a plans location) is not returned."""
    (tmp_path / "daemon" / "src").mkdir(parents=True)
    (tmp_path / "daemon" / "src" / "foo.md").write_text("arbitrary md")

    assert list_plans(tmp_path) == []


def test_list_plans_excludes_directories(tmp_path: Path) -> None:
    """A directory whose name matches the glob is not returned (only files)."""
    target = tmp_path / "docs" / "plans" / "sub.md"
    target.mkdir(parents=True)

    assert list_plans(tmp_path) == []


def test_list_plans_dot_plan_md_at_root(tmp_path: Path) -> None:
    """``*.plan.md`` files at the root are returned."""
    (tmp_path / "foo.plan.md").write_text("foo plan")

    assert list_plans(tmp_path) == ["foo.plan.md"]


def test_list_plans_root_name_non_plan_md_excluded(tmp_path: Path) -> None:
    """Root ``.md`` files that aren't one of the three named specs nor ``*.plan.md``
    are excluded (e.g., ``NOTES.md``)."""
    (tmp_path / "NOTES.md").write_text("notes")
    (tmp_path / "CHANGELOG.md").write_text("cl")

    assert list_plans(tmp_path) == []


def test_list_plans_dedup_when_file_matches_multiple_patterns(tmp_path: Path) -> None:
    """A file matching multiple conventional globs is returned only once.

    ``foo.plan.md`` living under ``docs/plans/`` matches both ``docs/plans/**/*.md``
    and ``*.plan.md``-style checks (depending on cwd); the ``set``-based dedup
    inside ``list_plans`` must guarantee one entry.
    """
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "foo.plan.md").write_text("x")

    result = list_plans(tmp_path)

    assert result.count("docs/plans/foo.plan.md") == 1


# ---------------------------------------------------------------------------
# _read_plan
# ---------------------------------------------------------------------------


def test_read_plan_returns_contents(tmp_path: Path) -> None:
    """Reading an existing plan under docs/plans/ returns its text contents."""
    target = tmp_path / "docs" / "plans" / "hello.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Hello plan\n")

    assert _read_plan(tmp_path, "docs/plans/hello.md") == "# Hello plan\n"


def test_read_plan_unicode_content_preserved(tmp_path: Path) -> None:
    """Unicode content (emojis, non-Latin scripts) is returned byte-for-byte."""
    target = tmp_path / "docs" / "plans" / "unicode.md"
    target.parent.mkdir(parents=True)
    content = "plan with emoji and kana\n"
    target.write_text(content, encoding="utf-8")

    assert _read_plan(tmp_path, "docs/plans/unicode.md") == content


def test_read_plan_root_spec_names(tmp_path: Path) -> None:
    """Each of CLAUDE.md / AGENTS.md / SPEC.md at the root is readable."""
    for name in ("CLAUDE.md", "AGENTS.md", "SPEC.md"):
        (tmp_path / name).write_text(f"contents of {name}")

    for name in ("CLAUDE.md", "AGENTS.md", "SPEC.md"):
        assert _read_plan(tmp_path, name) == f"contents of {name}"


def test_read_plan_root_dot_plan_md(tmp_path: Path) -> None:
    """``*.plan.md`` at the root is readable."""
    (tmp_path / "migration.plan.md").write_text("migration")

    assert _read_plan(tmp_path, "migration.plan.md") == "migration"


def test_read_plan_rejects_parent_traversal(tmp_path: Path) -> None:
    """A rel_path containing ``..`` that escapes project_root raises PermissionError."""
    (tmp_path / "secret.py").write_text("SECRET_KEY = 'xxx'")

    inner = tmp_path / "inner"
    inner.mkdir()

    with pytest.raises(PermissionError, match="escapes project root"):
        _read_plan(inner, "../secret.py")


def test_read_plan_rejects_absolute_path(tmp_path: Path) -> None:
    """An absolute rel_path like /etc/passwd is rejected as an escape."""
    # joining base with an absolute path in pathlib resolves to the absolute
    # target, which then fails relative_to(base).
    with pytest.raises(PermissionError, match="escapes project root"):
        _read_plan(tmp_path, "/etc/passwd")


def test_read_plan_missing_file_denied_as_non_plan(tmp_path: Path) -> None:
    """A non-existent ``docs/plans/foo.md`` is denied rather than raising
    ``FileNotFoundError``.

    Under the single-source-of-truth model, ``_read_plan`` asks
    ``_discover(base)`` whether the target is advertised — and ``_discover``
    only returns files that exist on disk. So a missing file in a legal plan
    location falls into the "not a plan or spec" branch, which is the
    desired behaviour: no existence oracle for arbitrary paths and no
    discrimination between "absent plan file" and "present non-plan file".
    """
    with pytest.raises(PermissionError, match="not a plan or spec"):
        _read_plan(tmp_path, "docs/plans/nonexistent.md")


def test_read_plan_non_plan_path_denied_even_if_file_exists(tmp_path: Path) -> None:
    """Paths outside the conventional patterns are denied with ``not a plan``."""
    src = tmp_path / "daemon" / "src"
    src.mkdir(parents=True)
    (src / "foo.py").write_text("import os")

    with pytest.raises(PermissionError, match="not a plan or spec"):
        _read_plan(tmp_path, "daemon/src/foo.py")


def test_read_plan_non_plan_md_denied(tmp_path: Path) -> None:
    """A .md file under a non-plans directory is denied."""
    (tmp_path / "daemon" / "src").mkdir(parents=True)
    (tmp_path / "daemon" / "src" / "foo.md").write_text("x")

    with pytest.raises(PermissionError, match="not a plan or spec"):
        _read_plan(tmp_path, "daemon/src/foo.md")


def test_read_plan_readme_denied(tmp_path: Path) -> None:
    """Root README.md is not a plan and is denied."""
    (tmp_path / "README.md").write_text("readme")

    with pytest.raises(PermissionError, match="not a plan or spec"):
        _read_plan(tmp_path, "README.md")


def test_read_plan_no_existence_oracle_for_non_plan_paths(tmp_path: Path) -> None:
    """Probing a non-plan path must raise ``PermissionError`` whether the
    file exists or not, so a caller cannot infer arbitrary file existence
    from the exception class.
    """
    (tmp_path / "daemon" / "src").mkdir(parents=True)
    (tmp_path / "daemon" / "src" / "present.py").write_text("x")
    # Present: denied as non-plan, NOT raised as FileNotFoundError.
    with pytest.raises(PermissionError, match="not a plan or spec"):
        _read_plan(tmp_path, "daemon/src/present.py")
    # Absent: same denial, same class — no discrimination.
    with pytest.raises(PermissionError, match="not a plan or spec"):
        _read_plan(tmp_path, "daemon/src/absent.py")


def test_read_plan_symlink_escape_rejected(tmp_path: Path) -> None:
    """A symlink under docs/plans/ that points outside project_root is rejected.

    ``Path.resolve()`` follows symlinks; ``relative_to(base)`` then catches the
    escape. The PermissionError must surface rather than silently reading the
    outside file.
    """
    outside_dir = tmp_path.parent / f"outside-{tmp_path.name}"
    outside_dir.mkdir()
    outside_secret = outside_dir / "secret.md"
    outside_secret.write_text("leaked")

    project = tmp_path / "project"
    (project / "docs" / "plans").mkdir(parents=True)
    link = project / "docs" / "plans" / "linked.md"
    link.symlink_to(outside_secret)

    try:
        with pytest.raises(PermissionError, match="escapes project root"):
            _read_plan(project, "docs/plans/linked.md")
    finally:
        # tmp_path cleanup only covers tmp_path itself, not tmp_path.parent
        outside_secret.unlink(missing_ok=True)
        outside_dir.rmdir()


def test_read_plan_symlink_to_file_inside_project_allowed(tmp_path: Path) -> None:
    """A symlink that resolves to a file inside project_root and matches a
    conventional pattern is allowed (symlink rejection is about escapes, not
    symlinks in general)."""
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    target = tmp_path / "docs" / "plans" / "real.md"
    target.write_text("real contents")
    link = tmp_path / "docs" / "plans" / "link.md"
    link.symlink_to(target)

    assert _read_plan(tmp_path, "docs/plans/link.md") == "real contents"


# ---------------------------------------------------------------------------
# C1 regression — matcher / discover parity (Bug 1: under-approximation)
# ---------------------------------------------------------------------------


def test_read_plan_deep_docs_plans_path_matches_discover_surface(tmp_path: Path) -> None:
    """_read_plan must accept any path list_plans advertises, regardless of depth.

    Regression for Bug 1: ``PurePath.match("docs/plans/**/*.md")`` rejects
    ``docs/plans/a/b/c/d.md`` (pathlib's match requires at least one ``**``
    segment), so the old separate-matcher design denied paths that
    ``Path.glob`` (used by ``_discover``) happily found. The single-source
    implementation must round-trip cleanly.
    """
    deep = tmp_path / "docs" / "plans" / "a" / "b" / "c" / "d.md"
    deep.parent.mkdir(parents=True)
    deep.write_text("deep plan contents")

    listed = list_plans(tmp_path)
    assert "docs/plans/a/b/c/d.md" in listed
    assert _read_plan(tmp_path, "docs/plans/a/b/c/d.md") == "deep plan contents"


def test_read_plan_deep_specs_path_matches_discover_surface(tmp_path: Path) -> None:
    """Same round-trip invariant for the ``specs/**/*.md`` branch."""
    deep = tmp_path / "specs" / "a" / "b" / "c.md"
    deep.parent.mkdir(parents=True)
    deep.write_text("deep spec contents")

    listed = list_plans(tmp_path)
    assert "specs/a/b/c.md" in listed
    assert _read_plan(tmp_path, "specs/a/b/c.md") == "deep spec contents"


# ---------------------------------------------------------------------------
# C1 regression — matcher / discover parity (Bug 2: over-approximation, SECURITY)
# ---------------------------------------------------------------------------


def test_read_plan_denies_agents_md_outside_root(tmp_path: Path) -> None:
    """``AGENTS.md`` is anchored to the project root; nested ``AGENTS.md`` is denied.

    Regression for Bug 2 (security): ``PurePath.match("AGENTS.md")`` is a
    tail match with no leading anchor, so ``nested/AGENTS.md`` matched under
    the old matcher and became readable even though ``list_plans`` (which
    uses ``base.glob("AGENTS.md")``) correctly anchored the pattern to the
    root. The single-source fix restores parity.
    """
    nested = tmp_path / "nested" / "AGENTS.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("attacker dropped")

    assert "nested/AGENTS.md" not in list_plans(tmp_path)
    with pytest.raises(PermissionError, match="not a plan or spec"):
        _read_plan(tmp_path, "nested/AGENTS.md")


def test_read_plan_denies_plan_md_outside_root(tmp_path: Path) -> None:
    """``*.plan.md`` is anchored to the root; deep ``*.plan.md`` is denied.

    Regression for Bug 2 (security): an attacker who drops
    ``daemon/src/secrets.plan.md`` must NOT become readable just because the
    suffix matches — ``base.glob("*.plan.md")`` anchors to the root, and the
    validation layer must agree.
    """
    hidden = tmp_path / "daemon" / "src" / "secrets.plan.md"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("attacker dropped")

    assert "daemon/src/secrets.plan.md" not in list_plans(tmp_path)
    with pytest.raises(PermissionError, match="not a plan or spec"):
        _read_plan(tmp_path, "daemon/src/secrets.plan.md")


def test_read_plan_denies_nested_claude_md(tmp_path: Path) -> None:
    """Belt-and-braces: nested ``CLAUDE.md`` is denied (same anchoring rule)."""
    nested = tmp_path / "nested" / "CLAUDE.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("nested claude")

    assert "nested/CLAUDE.md" not in list_plans(tmp_path)
    with pytest.raises(PermissionError, match="not a plan or spec"):
        _read_plan(tmp_path, "nested/CLAUDE.md")


def test_read_plan_denies_nested_spec_md(tmp_path: Path) -> None:
    """Belt-and-braces: nested ``SPEC.md`` is denied (same anchoring rule)."""
    nested = tmp_path / "nested" / "SPEC.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("nested spec")

    assert "nested/SPEC.md" not in list_plans(tmp_path)
    with pytest.raises(PermissionError, match="not a plan or spec"):
        _read_plan(tmp_path, "nested/SPEC.md")


# ---------------------------------------------------------------------------
# SDK wrapper behaviour (find_plans / read_plan @tool objects)
# ---------------------------------------------------------------------------


async def test_find_plans_tool_returns_text_block(tmp_path: Path) -> None:
    """The find_plans tool wraps list_plans output in the MCP text-block shape."""
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "a.md").write_text("a")
    (tmp_path / "CLAUDE.md").write_text("claude")

    find_plans, _read_plan_tool = _make_plans_tools(tmp_path)
    result = await find_plans.handler({})

    content = result["content"]
    assert isinstance(content, list)
    assert len(content) == 1
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "CLAUDE.md\ndocs/plans/a.md"


async def test_find_plans_tool_empty_result_has_placeholder(tmp_path: Path) -> None:
    """When no plans are found the text block reads ``(no plans found)``
    rather than being empty (easier for Claude to reason about)."""
    find_plans, _read_plan_tool = _make_plans_tools(tmp_path)
    result = await find_plans.handler({})

    content = result["content"]
    assert isinstance(content, list)
    assert content[0]["text"] == "(no plans found)"


async def test_read_plan_tool_returns_text_block(tmp_path: Path) -> None:
    """The read_plan tool returns file contents in a text block on the happy path."""
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "p.md").write_text("plan body")

    _find_plans, read_plan_tool = _make_plans_tools(tmp_path)
    result = await read_plan_tool.handler({"rel_path": "docs/plans/p.md"})

    assert "isError" not in result
    content = result["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "plan body"


async def test_read_plan_tool_surfaces_permission_error(tmp_path: Path) -> None:
    """A denied path returns isError=True with the reason in the text block."""
    (tmp_path / "daemon" / "src").mkdir(parents=True)
    (tmp_path / "daemon" / "src" / "foo.py").write_text("x")

    _find_plans, read_plan_tool = _make_plans_tools(tmp_path)
    result = await read_plan_tool.handler({"rel_path": "daemon/src/foo.py"})

    assert result.get("isError") is True
    content = result["content"]
    assert isinstance(content, list)
    text = content[0]["text"]
    assert "not a plan or spec" in text


async def test_read_plan_tool_surfaces_file_not_found(tmp_path: Path) -> None:
    """A missing plan file surfaces as isError=True with a file-not-found message."""
    _find_plans, read_plan_tool = _make_plans_tools(tmp_path)
    result = await read_plan_tool.handler({"rel_path": "docs/plans/missing.md"})

    assert result.get("isError") is True
    assert "error" in result["content"][0]["text"]


async def test_read_plan_tool_surfaces_escape_attempt(tmp_path: Path) -> None:
    """A path-escape attempt surfaces as isError=True rather than raising."""
    _find_plans, read_plan_tool = _make_plans_tools(tmp_path)
    result = await read_plan_tool.handler({"rel_path": "../../etc/passwd"})

    assert result.get("isError") is True
    assert "escapes project root" in result["content"][0]["text"]


async def test_read_plan_handler_returns_error_on_non_utf8_content(
    tmp_path: Path,
) -> None:
    """Binary / non-UTF-8 content returns isError rather than crashing the handler.

    Regression for I1: ``read_text(encoding='utf-8')`` on a ``\\xff`` byte
    raises ``UnicodeDecodeError``, which used to escape the
    ``(PermissionError, FileNotFoundError)`` except clause and crash the
    async handler uncaught.
    """
    binary = tmp_path / "docs" / "plans" / "binary.md"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\xff\xfe\x00\x01")  # invalid UTF-8

    _find_plans, read_plan_tool = _make_plans_tools(tmp_path)
    result = await read_plan_tool.handler({"rel_path": "docs/plans/binary.md"})

    assert result.get("isError") is True
    assert "content" in result
    assert result["content"][0]["type"] == "text"


async def test_read_plan_handler_returns_error_on_null_byte_in_path(
    tmp_path: Path,
) -> None:
    """Null byte in ``rel_path`` returns isError rather than crashing the handler.

    Regression for I2: ``Path.resolve()`` raises ``ValueError`` on paths
    containing embedded null bytes (``..\\x00/.env``). Under the old except
    clause that escaped the handler; the widened ``(OSError,
    UnicodeDecodeError, ValueError)`` catches it cleanly.
    """
    _find_plans, read_plan_tool = _make_plans_tools(tmp_path)
    result = await read_plan_tool.handler({"rel_path": "..\x00/.env"})

    assert result.get("isError") is True
    assert "content" in result
    assert result["content"][0]["type"] == "text"


# ---------------------------------------------------------------------------
# P1 from Codex re-review: server-bound scope (no LLM-forgeable root)
# ---------------------------------------------------------------------------


def test_find_plans_tool_signature_has_no_project_root(tmp_path: Path) -> None:
    """find_plans tool input schema exposes no project_root — LLM cannot forge scope."""
    find_plans, _read_plan_tool = _make_plans_tools(tmp_path)
    # ``input_schema`` is typed ``type | dict[str, Any]`` by the SDK; ``in``
    # only works on the dict case. Explicit fail (not ``assert isinstance``)
    # so this works under ``python -O`` and gives a clear message if the SDK
    # ever returns a Pydantic class instead of a dict.
    schema = _require_dict_schema(find_plans.input_schema, tool_name="find_plans")
    assert "project_root" not in schema


def test_read_plan_tool_signature_has_no_project_root(tmp_path: Path) -> None:
    """read_plan exposes only rel_path — LLM cannot forge scope."""
    _find_plans, read_plan_tool = _make_plans_tools(tmp_path)
    schema = _require_dict_schema(read_plan_tool.input_schema, tool_name="read_plan")
    assert "project_root" not in schema
    assert "rel_path" in schema


async def test_find_plans_tool_scoped_to_root(tmp_path: Path) -> None:
    """Server-bound root: find_plans returns only paths under the root the server was built for.

    Regression lock-in for the P1 in Codex re-review: under the previous
    contract the tool took ``project_root`` as an LLM-controlled argument, so
    a prompt-injected brain could call ``find_plans(project_root="/")`` and
    enumerate AGENTS.md/CLAUDE.md/SPEC.md anywhere readable. Binding the root
    at construction time closes that door: the ``scoped`` server has no way
    to see ``sibling/``'s plans because the parameter simply does not exist.
    """
    scoped = tmp_path / "scoped"
    scoped.mkdir()
    (scoped / "docs" / "plans").mkdir(parents=True)
    (scoped / "docs" / "plans" / "inside.md").write_text("inside")
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "docs" / "plans").mkdir(parents=True)
    (sibling / "docs" / "plans" / "outside.md").write_text("outside")

    find_plans, _read_plan_tool = _make_plans_tools(scoped)
    result = await find_plans.handler({})
    text = result["content"][0]["text"]
    assert "inside.md" in text
    assert "outside.md" not in text


# ---------------------------------------------------------------------------
# create_sdk_mcp_server integration (hello-world wiring check)
# ---------------------------------------------------------------------------


def test_plans_mcp_server_config_shape(tmp_path: Path) -> None:
    """The factory returns an McpSdkServerConfig with name='plans' and an instance."""
    config = plans_mcp_server(tmp_path)

    assert config["type"] == "sdk"
    assert config["name"] == "plans"
    assert config["instance"] is not None


def test_tool_objects_expose_expected_metadata(tmp_path: Path) -> None:
    """@tool-decorated wrappers carry the name/description used by the SDK.

    With the P1 re-review fix, ``project_root`` is no longer a tool argument;
    it's closed over at server construction. ``find_plans`` takes no args;
    ``read_plan`` takes only ``rel_path``.
    """
    find_plans, read_plan_tool = _make_plans_tools(tmp_path)
    assert find_plans.name == "find_plans"
    assert "plan" in find_plans.description.lower()
    assert find_plans.input_schema == {}

    assert read_plan_tool.name == "read_plan"
    assert "plan" in read_plan_tool.description.lower()
    assert read_plan_tool.input_schema == {"rel_path": str}


def test_conventional_patterns_locked() -> None:
    """The conventional plan/spec pattern set is the documented list.

    Changing this set changes the brain's read surface; treat as a contract.
    """
    assert set(_CONVENTIONAL_PATTERNS) == {
        "docs/plans/**/*.md",
        "specs/**/*.md",
        "AGENTS.md",
        "CLAUDE.md",
        "SPEC.md",
        "*.plan.md",
    }


# ---------------------------------------------------------------------------
# MCP server dispatch (I4: closes the registration blind spot)
# ---------------------------------------------------------------------------


async def test_plans_mcp_server_dispatches_through_registered_handlers(
    tmp_path: Path,
) -> None:
    """Route a synthetic ``tools/list`` + ``tools/call`` through the created
    server instance to prove that ``find_plans`` and ``read_plan`` are
    actually reachable via the MCP request path — not merely exposed as
    ``@tool`` objects in the process.

    Regression for I4: the previous integration test was shape-only
    (``config['type'] == 'sdk'``, tool metadata). That can't catch a tool
    registered twice, skipped, or mis-named in the decorator — the MCP
    dispatcher would still fail at runtime. This test exercises the real
    request handlers the SDK hands to Claude.

    The instance is an ``mcp.server.lowlevel.server.Server`` with a
    ``request_handlers`` mapping keyed by the MCP request types. We invoke
    ``ListToolsRequest`` and ``CallToolRequest`` handlers directly, mirroring
    what the SDK transport does. ``ServerResult`` is a Pydantic ``RootModel``
    union of every response shape, so we narrow with ``isinstance`` to keep
    strict mypy happy while also asserting the actual response class.
    """
    from mcp import types as mcp_types

    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "a.md").write_text("alpha plan")

    config = plans_mcp_server(tmp_path)
    instance = config["instance"]

    list_handler = instance.request_handlers[mcp_types.ListToolsRequest]
    list_result = await list_handler(mcp_types.ListToolsRequest(method="tools/list", params=None))
    list_payload = list_result.root
    assert isinstance(list_payload, mcp_types.ListToolsResult)
    tool_names = {t.name for t in list_payload.tools}
    assert {"find_plans", "read_plan"} <= tool_names

    call_handler = instance.request_handlers[mcp_types.CallToolRequest]
    find_result = await call_handler(
        mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="find_plans",
                arguments={},
            ),
        )
    )
    find_payload = find_result.root
    assert isinstance(find_payload, mcp_types.CallToolResult)
    assert find_payload.isError is False
    find_block = find_payload.content[0]
    assert isinstance(find_block, mcp_types.TextContent)
    assert "docs/plans/a.md" in find_block.text

    read_result = await call_handler(
        mcp_types.CallToolRequest(
            method="tools/call",
            params=mcp_types.CallToolRequestParams(
                name="read_plan",
                arguments={"rel_path": "docs/plans/a.md"},
            ),
        )
    )
    read_payload = read_result.root
    assert isinstance(read_payload, mcp_types.CallToolResult)
    assert read_payload.isError is False
    read_block = read_payload.content[0]
    assert isinstance(read_block, mcp_types.TextContent)
    assert read_block.text == "alpha plan"
