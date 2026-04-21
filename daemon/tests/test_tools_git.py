from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from reachy_ducky_daemon.tools.git import GitTool


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Initialize a minimal git repo in ``tmp_path`` with one committed file."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("hello")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_git_status_clean(tmp_repo: Path) -> None:
    """GitTool.status() on a fresh repo reports a clean working tree."""
    out = GitTool(tmp_repo).status()
    assert "nothing to commit" in out or "working tree clean" in out


def test_git_log_returns_init_commit(tmp_repo: Path) -> None:
    """GitTool.log() surfaces the seeded ``init`` commit subject."""
    out = GitTool(tmp_repo).log(limit=10)
    assert "init" in out


def test_git_diff_shows_uncommitted(tmp_repo: Path) -> None:
    """GitTool.diff() includes added lines from an uncommitted edit."""
    (tmp_repo / "a.txt").write_text("hello world")
    out = GitTool(tmp_repo).diff()
    assert "+hello world" in out


def test_git_rejects_write_commands(tmp_repo: Path) -> None:
    """GitTool.run() raises ValueError with 'read-only' for mutating subcommands like push."""
    tool = GitTool(tmp_repo)
    with pytest.raises(ValueError, match="read-only"):
        tool.run(["push", "origin", "main"])
