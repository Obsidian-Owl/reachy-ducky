"""Unit tests for the in-process plans MCP server.

Exercises the pure helpers (``_list_plans``, ``_read_plan``) directly so the
security properties are locked in without going through the SDK layer, plus a
hello-world integration assertion that the @tool-decorated wrappers are wired
through ``create_sdk_mcp_server`` with the right names/descriptions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from reachy_ducky_daemon.brain.plans_mcp import (
    _CONVENTIONAL_PATTERNS,
    _list_plans,
    _read_plan,
    find_plans,
    plans_mcp_server,
    read_plan,
)

# ---------------------------------------------------------------------------
# _list_plans
# ---------------------------------------------------------------------------


def test_list_plans_empty_project(tmp_path: Path) -> None:
    """An empty project tree returns an empty list."""
    assert _list_plans(tmp_path) == []


def test_list_plans_single_docs_plan(tmp_path: Path) -> None:
    """A plan under docs/plans/ is discovered and returned relative to root."""
    plan = tmp_path / "docs" / "plans" / "foo.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# foo")

    assert _list_plans(tmp_path) == ["docs/plans/foo.md"]


def test_list_plans_nested_docs_plans(tmp_path: Path) -> None:
    """Recursive ``**`` glob picks up plans in subdirectories under docs/plans/."""
    (tmp_path / "docs" / "plans" / "sub").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "sub" / "deep.md").write_text("deep")

    assert _list_plans(tmp_path) == ["docs/plans/sub/deep.md"]


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

    result = _list_plans(tmp_path)

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

    assert _list_plans(tmp_path) == []


def test_list_plans_daemon_src_excluded(tmp_path: Path) -> None:
    """Markdown under daemon/src/ (not a plans location) is not returned."""
    (tmp_path / "daemon" / "src").mkdir(parents=True)
    (tmp_path / "daemon" / "src" / "foo.md").write_text("arbitrary md")

    assert _list_plans(tmp_path) == []


def test_list_plans_excludes_directories(tmp_path: Path) -> None:
    """A directory whose name matches the glob is not returned (only files)."""
    target = tmp_path / "docs" / "plans" / "sub.md"
    target.mkdir(parents=True)

    assert _list_plans(tmp_path) == []


def test_list_plans_dot_plan_md_at_root(tmp_path: Path) -> None:
    """``*.plan.md`` files at the root are returned."""
    (tmp_path / "foo.plan.md").write_text("foo plan")

    assert _list_plans(tmp_path) == ["foo.plan.md"]


def test_list_plans_root_name_non_plan_md_excluded(tmp_path: Path) -> None:
    """Root ``.md`` files that aren't one of the three named specs nor ``*.plan.md``
    are excluded (e.g., ``NOTES.md``)."""
    (tmp_path / "NOTES.md").write_text("notes")
    (tmp_path / "CHANGELOG.md").write_text("cl")

    assert _list_plans(tmp_path) == []


def test_list_plans_dedup_when_file_matches_multiple_patterns(tmp_path: Path) -> None:
    """A file matching multiple conventional globs is returned only once.

    ``foo.plan.md`` living under ``docs/plans/`` matches both ``docs/plans/**/*.md``
    and ``*.plan.md``-style checks (depending on cwd); the ``set``-based dedup
    inside ``_list_plans`` must guarantee one entry.
    """
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "foo.plan.md").write_text("x")

    result = _list_plans(tmp_path)

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


def test_read_plan_missing_file(tmp_path: Path) -> None:
    """Missing file under a legal plans path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
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
# SDK wrapper behaviour (find_plans / read_plan @tool objects)
# ---------------------------------------------------------------------------


async def test_find_plans_tool_returns_text_block(tmp_path: Path) -> None:
    """The find_plans tool wraps _list_plans output in the MCP text-block shape."""
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "a.md").write_text("a")
    (tmp_path / "CLAUDE.md").write_text("claude")

    result = await find_plans.handler({"project_root": str(tmp_path)})

    content = result["content"]
    assert isinstance(content, list)
    assert len(content) == 1
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "CLAUDE.md\ndocs/plans/a.md"


async def test_find_plans_tool_empty_result_has_placeholder(tmp_path: Path) -> None:
    """When no plans are found the text block reads ``(no plans found)``
    rather than being empty (easier for Claude to reason about)."""
    result = await find_plans.handler({"project_root": str(tmp_path)})

    content = result["content"]
    assert isinstance(content, list)
    assert content[0]["text"] == "(no plans found)"


async def test_read_plan_tool_returns_text_block(tmp_path: Path) -> None:
    """The read_plan tool returns file contents in a text block on the happy path."""
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "p.md").write_text("plan body")

    result = await read_plan.handler({"project_root": str(tmp_path), "rel_path": "docs/plans/p.md"})

    assert "isError" not in result
    content = result["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "plan body"


async def test_read_plan_tool_surfaces_permission_error(tmp_path: Path) -> None:
    """A denied path returns isError=True with the reason in the text block."""
    (tmp_path / "daemon" / "src").mkdir(parents=True)
    (tmp_path / "daemon" / "src" / "foo.py").write_text("x")

    result = await read_plan.handler(
        {"project_root": str(tmp_path), "rel_path": "daemon/src/foo.py"}
    )

    assert result.get("isError") is True
    content = result["content"]
    assert isinstance(content, list)
    text = content[0]["text"]
    assert "not a plan or spec" in text


async def test_read_plan_tool_surfaces_file_not_found(tmp_path: Path) -> None:
    """A missing plan file surfaces as isError=True with a file-not-found message."""
    result = await read_plan.handler(
        {"project_root": str(tmp_path), "rel_path": "docs/plans/missing.md"}
    )

    assert result.get("isError") is True
    assert "error" in result["content"][0]["text"]


async def test_read_plan_tool_surfaces_escape_attempt(tmp_path: Path) -> None:
    """A path-escape attempt surfaces as isError=True rather than raising."""
    result = await read_plan.handler(
        {"project_root": str(tmp_path), "rel_path": "../../etc/passwd"}
    )

    assert result.get("isError") is True
    assert "escapes project root" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# create_sdk_mcp_server integration (hello-world wiring check)
# ---------------------------------------------------------------------------


def test_plans_mcp_server_config_shape() -> None:
    """The factory returns an McpSdkServerConfig with name='plans' and an instance."""
    config = plans_mcp_server()

    assert config["type"] == "sdk"
    assert config["name"] == "plans"
    assert config["instance"] is not None


def test_tool_objects_expose_expected_metadata() -> None:
    """@tool-decorated wrappers carry the name/description used by the SDK."""
    assert find_plans.name == "find_plans"
    assert "plan" in find_plans.description.lower()
    assert find_plans.input_schema == {"project_root": str}

    assert read_plan.name == "read_plan"
    assert "plan" in read_plan.description.lower()
    assert read_plan.input_schema == {"project_root": str, "rel_path": str}


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
