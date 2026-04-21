"""Filesystem scaffolder for the Reachy Ducky memory layout."""

from __future__ import annotations

from pathlib import Path

from . import templates


def _write_if_missing(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content)


def ensure_layout(root: Path) -> None:
    """Seed the canonical ducky/human tree + empty projects dir under ``root``."""
    root = Path(root)
    _write_if_missing(root / "ducky" / "soul.md", templates.SOUL_MD)
    _write_if_missing(root / "ducky" / "core-blocks" / "stances.md", templates.STANCES_MD)
    _write_if_missing(
        root / "ducky" / "core-blocks" / "running-jokes.md", templates.RUNNING_JOKES_MD
    )
    _write_if_missing(root / "ducky" / "core-blocks" / "open-threads.md", templates.OPEN_THREADS_MD)
    _write_if_missing(root / "human" / "user.md", templates.USER_MD)
    _write_if_missing(root / "human" / "feedback.md", templates.FEEDBACK_MD)
    _write_if_missing(root / "human" / "preferences.md", templates.PREFERENCES_MD)
    (root / "projects").mkdir(parents=True, exist_ok=True)


def ensure_project(root: Path, slug: str) -> Path:
    """Seed the per-project tree under ``root/projects/<slug>/`` and return its path."""
    proj = Path(root) / "projects" / slug
    _write_if_missing(proj / "project.md", templates.PROJECT_MD.format(slug=slug))
    _write_if_missing(proj / "people.md", templates.PEOPLE_MD)
    _write_if_missing(proj / "decisions.md", templates.DECISIONS_MD)
    _write_if_missing(proj / "concerns.md", templates.CONCERNS_MD)
    (proj / "branches").mkdir(parents=True, exist_ok=True)
    return proj
