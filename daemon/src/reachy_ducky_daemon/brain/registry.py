"""Per-slug lazy-build cache of :class:`BrainInterface` instances.

The registry decouples the server from any particular brain implementation:
``create_app`` only needs ``brain_for(slug)`` and ``path_for(slug)``. The
factory callable is supplied at construction time so tests can hand in a
``MockBrain``-producing lambda while ``main()`` hands in a Pattern-B
``ClaudeSDKBrain.with_tools(...)`` factory.

Brains are built the first time a slug is looked up and cached thereafter,
so flipping between projects on a running daemon does not rebuild the
(expensive) ``ClaudeAgentOptions`` every query.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from reachy_ducky_daemon.project import Project

from .interface import BrainInterface
from .mock import MockBrain

__all__ = ["BrainFactory", "BrainRegistry"]

BrainFactory = Callable[[Project], BrainInterface]


class BrainRegistry:
    """Lazily builds and caches one brain per project slug."""

    def __init__(self, *, projects: list[Project], build_brain: BrainFactory) -> None:
        """Register ``projects`` and remember how to ``build_brain`` for each.

        Args:
            projects: Zero or more :class:`Project` instances. An empty list
                produces a "degraded" registry whose lookups all raise
                :class:`KeyError`; this is how the daemon boots without any
                configured project (degraded mode — caller should log it).
            build_brain: Called with the :class:`Project` the first time a
                slug is requested via :meth:`brain_for`. Its return value is
                cached; subsequent lookups for the same slug return the same
                instance.

        Raises:
            ValueError: if two entries in ``projects`` share a slug, or if
                more than one entry has ``primary=True``.
        """
        self._projects: dict[str, Project] = {p.slug: p for p in projects}
        if len(self._projects) != len(projects):
            raise ValueError("duplicate project slugs")
        primaries = [p for p in projects if p.primary]
        if len(primaries) > 1:
            raise ValueError(
                f"at most one project may be primary; got {[p.slug for p in primaries]}"
            )
        self._primary_slug: str | None = primaries[0].slug if primaries else None
        self._build_brain = build_brain
        self._brains: dict[str, BrainInterface] = {}

    def brain_for(self, slug: str) -> BrainInterface:
        """Return the cached brain for ``slug``, building it on first access.

        Concurrency invariant: BrainFactory must be safe to call from a single
        event-loop thread with no cross-request locking. Today this holds —
        ``ClaudeSDKBrain.with_tools()`` is documented as pure config assembly
        with no live Claude, subprocess, filesystem, or network I/O at call
        time (see brain/options.py). MCP subprocesses spawn at tool dispatch,
        not at construction. If a future BrainFactory ever spawns resources at
        construction time (e.g., eager MCP boot, disk cache warm-up), this
        method needs an asyncio.Lock because FastAPI can dispatch two queries
        with the same unbuilt slug onto the same event loop simultaneously.
        """
        if slug not in self._projects:
            raise KeyError(slug)
        if slug not in self._brains:
            self._brains[slug] = self._build_brain(self._projects[slug])
        return self._brains[slug]

    def path_for(self, slug: str) -> Path:
        """Return the on-disk path for ``slug`` (no brain build)."""
        if slug not in self._projects:
            raise KeyError(slug)
        return self._projects[slug].path

    def project_for(self, slug: str) -> Project:
        """Return the full :class:`Project` for ``slug`` (no brain build).

        The ``/specialists/pr-reviewer`` route needs ``github_repo`` in
        addition to ``path``; keeping a single lookup instead of two
        keeps the registry's immutability contract simple (one KeyError
        path, one project instance).
        """
        if slug not in self._projects:
            raise KeyError(slug)
        return self._projects[slug]

    def primary_slug(self) -> str | None:
        """Return the ``primary=True`` project slug, or ``None`` if none configured."""
        return self._primary_slug

    def slugs(self) -> list[str]:
        """Return every configured slug, alphabetically sorted."""
        return sorted(self._projects.keys())

    def built_slugs(self) -> list[str]:
        """Return every slug whose brain has actually been built.

        Lets ``/health`` peek at already-built brains without triggering a
        lazy build just to report a class name.
        """
        return sorted(self._brains.keys())

    @classmethod
    def single_mock(cls, slug: str, path: Path) -> BrainRegistry:
        """Test helper: single-project registry with a :class:`MockBrain` factory.

        The returned registry has one primary project at ``slug`` rooted at
        ``path``; its brain factory produces a fresh :class:`MockBrain` per
        build (only one is ever built because there's only one slug).
        """
        return cls(
            projects=[Project(slug=slug, path=path, primary=True)],
            build_brain=lambda _p: MockBrain(),
        )
