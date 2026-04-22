"""Per-project configuration value object.

A :class:`Project` binds a short ``slug`` (routing key used by
``/brain/query`` and ``/specialists/*``) to the absolute project path on
disk plus optional metadata (``github_repo``, ``primary`` flag).

Validation runs at construction time via
:meth:`__post_init__`: invalid slugs, relative paths, and malformed
``github_repo`` values raise :class:`ValueError` before the object
escapes into the registry or the HTTP layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Project"]

# Lowercase kebab-case; must start with [a-z0-9] so slugs never begin
# with a separator. Length-bounded (1-64) because slugs are wire-visible
# (BrainRequest.project_slug, HealthResponse.projects, log lines) and an
# unbounded slug is a log-flood and response-size vector.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True)
class Project:
    """Per-project configuration bound to a registry slug.

    Instances are frozen so they can be shared across threads (FastAPI
    worker pool) without defensive copies. Construction expands ``~``
    and resolves ``path`` to an absolute form; a relative path survives
    the expansion is rejected as a bug in the caller.
    """

    slug: str
    path: Path
    github_repo: str | None = None
    primary: bool = False

    def __post_init__(self) -> None:
        if not self.slug or not _SLUG_RE.match(self.slug):
            raise ValueError(
                f"slug must match {_SLUG_RE.pattern!r} "
                f"(lowercase kebab, starts alnum), got {self.slug!r}"
            )
        expanded = self.path.expanduser()
        if not expanded.is_absolute():
            raise ValueError(f"path must be absolute after ~ expansion, got {self.path!r}")
        # `resolve(strict=False)` — directory may not exist yet at config time;
        # we only need the canonical absolute form for routing.
        resolved = expanded.resolve()
        # ``self`` is frozen; rebind via object.__setattr__ the way the
        # stdlib dataclass guide documents for post-init normalization.
        object.__setattr__(self, "path", resolved)

        if self.github_repo is not None:
            parts = self.github_repo.split("/")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(f"github_repo must be 'owner/repo', got {self.github_repo!r}")
