"""Tests for the memory directory scaffolder (ensure_layout / ensure_project)."""

from __future__ import annotations

from pathlib import Path

from reachy_ducky_daemon.memory.layout import ensure_layout, ensure_project


def test_ensure_layout_creates_expected_tree(tmp_path: Path) -> None:
    """ensure_layout seeds the canonical ducky/human tree + empty projects dir."""
    ensure_layout(tmp_path)
    assert (tmp_path / "ducky" / "soul.md").exists()
    assert (tmp_path / "ducky" / "core-blocks" / "stances.md").exists()
    assert (tmp_path / "ducky" / "core-blocks" / "running-jokes.md").exists()
    assert (tmp_path / "ducky" / "core-blocks" / "open-threads.md").exists()
    assert (tmp_path / "human" / "user.md").exists()
    assert (tmp_path / "human" / "feedback.md").exists()
    assert (tmp_path / "human" / "preferences.md").exists()
    assert (tmp_path / "projects").is_dir()


def test_ensure_project_creates_per_project_tree(tmp_path: Path) -> None:
    """ensure_project seeds per-project files + an empty branches/ dir."""
    ensure_layout(tmp_path)
    ensure_project(tmp_path, slug="reachy-ducky")
    root = tmp_path / "projects" / "reachy-ducky"
    assert (root / "project.md").exists()
    assert (root / "people.md").exists()
    assert (root / "decisions.md").exists()
    assert (root / "concerns.md").exists()
    assert (root / "branches").is_dir()


def test_ensure_layout_is_idempotent(tmp_path: Path) -> None:
    """Re-running ensure_layout must not overwrite edited seed files."""
    ensure_layout(tmp_path)
    (tmp_path / "ducky" / "soul.md").write_text("# edited")
    ensure_layout(tmp_path)
    assert (tmp_path / "ducky" / "soul.md").read_text() == "# edited"
