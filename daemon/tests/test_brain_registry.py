"""Tests for :class:`BrainRegistry` lifecycle and lookup semantics."""

from __future__ import annotations

from pathlib import Path

import pytest
from reachy_ducky_daemon.brain.interface import BrainInterface
from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_daemon.brain.registry import BrainRegistry
from reachy_ducky_daemon.project import Project


def test_brain_for_builds_lazily_and_caches(tmp_path: Path) -> None:
    """First ``brain_for`` builds; second returns the same instance (identity)."""
    project = Project(slug="demo", path=tmp_path)
    seen: list[Project] = []

    def build(p: Project) -> BrainInterface:
        seen.append(p)
        return MockBrain()

    reg = BrainRegistry(projects=[project], build_brain=build)
    assert seen == []  # still lazy
    b1 = reg.brain_for("demo")
    b2 = reg.brain_for("demo")
    assert b1 is b2
    assert len(seen) == 1
    assert seen[0].slug == "demo"


def test_brain_for_raises_on_unknown_slug(tmp_path: Path) -> None:
    """Unknown slugs raise :class:`KeyError` (the HTTP layer maps this to 404)."""
    reg = BrainRegistry(
        projects=[Project(slug="demo", path=tmp_path)],
        build_brain=lambda _: MockBrain(),
    )
    with pytest.raises(KeyError):
        reg.brain_for("nope")


def test_path_for_returns_project_path(tmp_path: Path) -> None:
    """``path_for`` returns the resolved Project.path — no brain build."""
    reg = BrainRegistry(
        projects=[Project(slug="demo", path=tmp_path)],
        build_brain=lambda _: MockBrain(),
    )
    assert reg.path_for("demo") == tmp_path.resolve()
    assert reg.built_slugs() == []  # path lookup did NOT trigger a build


def test_path_for_raises_on_unknown_slug(tmp_path: Path) -> None:
    """Unknown slug also surfaces as :class:`KeyError` from ``path_for``."""
    reg = BrainRegistry(
        projects=[Project(slug="demo", path=tmp_path)],
        build_brain=lambda _: MockBrain(),
    )
    with pytest.raises(KeyError):
        reg.path_for("nope")


def test_primary_slug_returns_primary(tmp_path: Path) -> None:
    """With one primary flag set, ``primary_slug`` returns that slug."""
    reg = BrainRegistry(
        projects=[
            Project(slug="a", path=tmp_path / "a", primary=False),
            Project(slug="b", path=tmp_path / "b", primary=True),
            Project(slug="c", path=tmp_path / "c", primary=False),
        ],
        build_brain=lambda _: MockBrain(),
    )
    for p in ["a", "b", "c"]:
        (tmp_path / p).mkdir()
    # Recreate after mkdir so resolve() sees the dirs (it doesn't need them but
    # keeps the test intent explicit).
    assert reg.primary_slug() == "b"


def test_primary_slug_none_when_no_primary(tmp_path: Path) -> None:
    """Zero primaries → ``primary_slug`` is ``None`` (degraded-mode flag)."""
    reg = BrainRegistry(
        projects=[Project(slug="demo", path=tmp_path)],
        build_brain=lambda _: MockBrain(),
    )
    assert reg.primary_slug() is None


def test_slugs_returns_sorted(tmp_path: Path) -> None:
    """``slugs`` returns every configured slug, alphabetically sorted."""
    for d in ["zebra", "alpha", "middle"]:
        (tmp_path / d).mkdir()
    reg = BrainRegistry(
        projects=[
            Project(slug="zebra", path=tmp_path / "zebra"),
            Project(slug="alpha", path=tmp_path / "alpha"),
            Project(slug="middle", path=tmp_path / "middle"),
        ],
        build_brain=lambda _: MockBrain(),
    )
    assert reg.slugs() == ["alpha", "middle", "zebra"]


def test_duplicate_slugs_raises(tmp_path: Path) -> None:
    """Two entries with the same slug is a config error surfaced at construction."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    with pytest.raises(ValueError, match="duplicate project slugs"):
        BrainRegistry(
            projects=[
                Project(slug="demo", path=tmp_path / "a"),
                Project(slug="demo", path=tmp_path / "b"),
            ],
            build_brain=lambda _: MockBrain(),
        )


def test_two_primaries_raises(tmp_path: Path) -> None:
    """Two primaries is a config error — the server can't route an unslugged request."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    with pytest.raises(ValueError, match="at most one project may be primary"):
        BrainRegistry(
            projects=[
                Project(slug="a", path=tmp_path / "a", primary=True),
                Project(slug="b", path=tmp_path / "b", primary=True),
            ],
            build_brain=lambda _: MockBrain(),
        )


def test_empty_projects_list_is_allowed(tmp_path: Path) -> None:
    """Empty registry constructs successfully; lookups raise KeyError."""
    reg = BrainRegistry(projects=[], build_brain=lambda _: MockBrain())
    assert reg.slugs() == []
    assert reg.primary_slug() is None
    assert reg.built_slugs() == []
    with pytest.raises(KeyError):
        reg.brain_for("anything")


def test_single_mock_helper(tmp_path: Path) -> None:
    """``single_mock`` builds a working registry with MockBrain."""
    reg = BrainRegistry.single_mock("demo", tmp_path)
    assert reg.slugs() == ["demo"]
    assert reg.primary_slug() == "demo"
    assert reg.path_for("demo") == tmp_path.resolve()
    brain = reg.brain_for("demo")
    assert isinstance(brain, MockBrain)
    assert reg.brain_for("demo") is brain  # identity on second call


def test_built_slugs_tracks_lazy_builds(tmp_path: Path) -> None:
    """``built_slugs`` lets ``/health`` peek at what's been materialised without forcing a build."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    reg = BrainRegistry(
        projects=[
            Project(slug="a", path=tmp_path / "a", primary=True),
            Project(slug="b", path=tmp_path / "b"),
        ],
        build_brain=lambda _: MockBrain(),
    )
    assert reg.built_slugs() == []
    reg.brain_for("a")
    assert reg.built_slugs() == ["a"]
    reg.brain_for("b")
    assert reg.built_slugs() == ["a", "b"]


def test_factory_receives_project(tmp_path: Path) -> None:
    """The factory callable gets the full Project (not just the path)."""
    received: list[Project] = []

    def build(p: Project) -> BrainInterface:
        received.append(p)
        return MockBrain()

    project = Project(
        slug="demo",
        path=tmp_path,
        github_repo="Obsidian-Owl/demo",
        primary=True,
    )
    reg = BrainRegistry(projects=[project], build_brain=build)
    reg.brain_for("demo")
    assert len(received) == 1
    assert received[0].slug == "demo"
    assert received[0].github_repo == "Obsidian-Owl/demo"


def test_project_for_returns_project(tmp_path: Path) -> None:
    """``project_for`` returns the full Project — route needs github_repo, not just path."""
    project = Project(
        slug="demo",
        path=tmp_path,
        github_repo="Obsidian-Owl/demo",
        primary=True,
    )
    reg = BrainRegistry(projects=[project], build_brain=lambda _: MockBrain())

    fetched = reg.project_for("demo")
    assert fetched.slug == "demo"
    assert fetched.github_repo == "Obsidian-Owl/demo"
    assert reg.built_slugs() == []  # project lookup must NOT trigger a brain build


def test_project_for_raises_on_unknown_slug(tmp_path: Path) -> None:
    """Unknown slug raises :class:`KeyError` (HTTP layer maps to 404, same as the others)."""
    reg = BrainRegistry(
        projects=[Project(slug="demo", path=tmp_path)],
        build_brain=lambda _: MockBrain(),
    )
    with pytest.raises(KeyError):
        reg.project_for("nope")
