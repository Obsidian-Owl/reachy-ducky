"""Tests for :class:`Project` — slug / path / github_repo validation."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from reachy_ducky_daemon.project import Project


def test_project_accepts_valid_inputs(tmp_path: Path) -> None:
    """A happy-path construction produces a frozen instance with all fields populated."""
    p = Project(
        slug="reachy-ducky",
        path=tmp_path,
        github_repo="Obsidian-Owl/reachy-ducky",
        primary=True,
    )
    assert p.slug == "reachy-ducky"
    # Path survives `resolve()` round-trip (equality, not identity).
    assert p.path == tmp_path.resolve()
    assert p.github_repo == "Obsidian-Owl/reachy-ducky"
    assert p.primary is True


def test_project_is_frozen(tmp_path: Path) -> None:
    """``frozen=True`` means field reassignment raises :class:`FrozenInstanceError`."""
    p = Project(slug="demo", path=tmp_path)
    with pytest.raises(FrozenInstanceError):
        p.slug = "other"  # type: ignore[misc]


def test_project_expands_tilde_in_path() -> None:
    """``~`` is expanded at construction so no callsite has to remember to."""
    p = Project(slug="demo", path=Path("~/Projects/demo"))
    assert not str(p.path).startswith("~")
    assert p.path.is_absolute()


def test_project_resolves_path_to_absolute(tmp_path: Path) -> None:
    """A nested ``.``/``..`` path is canonicalised."""
    nested = tmp_path / "sub" / ".." / "sub"
    nested.mkdir(parents=True, exist_ok=True)
    p = Project(slug="demo", path=nested)
    assert p.path == (tmp_path / "sub").resolve()


def test_project_rejects_relative_path() -> None:
    """A relative path that doesn't expand to absolute is a caller bug."""
    with pytest.raises(ValueError, match="must be absolute"):
        Project(slug="demo", path=Path("relative/dir"))


@pytest.mark.parametrize(
    "bad_slug",
    [
        "",  # empty
        "-leading-dash",  # leading separator
        "UPPERCASE",  # uppercase
        "with space",  # space
        "under_score",  # underscore
        "dots.in.it",  # dots
        "slash/in-it",  # slash
    ],
)
def test_project_rejects_invalid_slug(tmp_path: Path, bad_slug: str) -> None:
    """Slug validation is strict kebab-case."""
    with pytest.raises(ValueError, match="slug must match"):
        Project(slug=bad_slug, path=tmp_path)


@pytest.mark.parametrize(
    "good_slug",
    [
        "a",  # single char
        "0",  # leading digit (regex allows [a-z0-9] at start)
        "reachy-ducky",  # standard kebab
        "x-y-z",  # multiple dashes
        "abc123",  # mixed alnum
    ],
)
def test_project_accepts_valid_slug(tmp_path: Path, good_slug: str) -> None:
    """The positive boundary of the slug regex."""
    p = Project(slug=good_slug, path=tmp_path)
    assert p.slug == good_slug


def test_project_accepts_slug_at_length_bound(tmp_path: Path) -> None:
    """64-character slug is the top of the allowed range."""
    slug = "a" + "b" * 63  # 64 chars total
    p = Project(slug=slug, path=tmp_path)
    assert p.slug == slug


def test_project_rejects_slug_over_length_bound(tmp_path: Path) -> None:
    """65+ character slugs are rejected so slugs can't flood logs or responses."""
    slug = "a" + "b" * 64  # 65 chars
    with pytest.raises(ValueError, match="slug"):
        Project(slug=slug, path=tmp_path)


@pytest.mark.parametrize(
    "bad_repo",
    [
        "owner",  # no slash
        "owner/",  # empty repo
        "/repo",  # empty owner
        "a/b/c",  # too many segments
        "",  # empty string (not None)
    ],
)
def test_project_rejects_malformed_github_repo(tmp_path: Path, bad_repo: str) -> None:
    """``github_repo`` must match ``owner/repo`` exactly when provided."""
    with pytest.raises(ValueError, match="github_repo must be"):
        Project(slug="demo", path=tmp_path, github_repo=bad_repo)


def test_project_accepts_none_github_repo(tmp_path: Path) -> None:
    """``github_repo=None`` is the default and must not trigger validation."""
    p = Project(slug="demo", path=tmp_path)
    assert p.github_repo is None


def test_project_defaults_primary_false(tmp_path: Path) -> None:
    """``primary`` defaults to False so most projects don't need to opt in."""
    p = Project(slug="demo", path=tmp_path)
    assert p.primary is False
