"""Daemon configuration — TOML (``~/.reachy-ducky/config.toml``) + env-var overrides.

Precedence (most → least authoritative):

1. Environment variables (``REACHY_DUCKY_*``).
2. TOML file at ``$REACHY_DUCKY_CONFIG`` or ``~/.reachy-ducky/config.toml``.
3. Hard-coded defaults in :class:`AppConfig`.

If no TOML file is present and no env vars set, the daemon still boots
with a zero-project "degraded" registry — the caller must ship a
descriptive error when ``/brain/query`` arrives with no slug and no
primary.
"""

from __future__ import annotations

import logging
import os
import tomllib  # Python 3.11+
from dataclasses import dataclass, field
from pathlib import Path

from .project import Project

__all__ = ["AppConfig"]

logger = logging.getLogger(__name__)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass
class AppConfig:
    """Runtime configuration for the daemon.

    Construction via :meth:`load` — :class:`AppConfig()` directly is valid
    only in tests where the caller supplies every field.
    """

    memory_root: Path
    host: str = "127.0.0.1"
    port: int = 8765
    auth_token: str | None = None
    projects: list[Project] = field(default_factory=list)

    @classmethod
    def default_path(cls) -> Path:
        """Resolve the default TOML config path.

        Honors ``$REACHY_DUCKY_CONFIG`` when set; otherwise
        ``~/.reachy-ducky/config.toml``.
        """
        return Path(
            os.environ.get(
                "REACHY_DUCKY_CONFIG",
                str(Path.home() / ".reachy-ducky" / "config.toml"),
            )
        ).expanduser()

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        """Load config from TOML (if present) + env-var overrides.

        Args:
            path: Override for the TOML path. ``None`` means "use
                :meth:`default_path`". Missing file is not an error —
                produces an env-only config (backward-compat with the
                pre-6.4 env-only boot path).

        Returns:
            A fully-populated :class:`AppConfig`.
        """
        cfg = cls(memory_root=Path.home() / ".reachy-ducky" / "memory")
        toml_path = path if path is not None else cls.default_path()
        if toml_path.exists():
            cfg = cls._apply_toml(cfg, toml_path)
        cfg = cls._apply_env(cfg)
        return cfg

    @classmethod
    def _apply_toml(cls, cfg: AppConfig, path: Path) -> AppConfig:
        with path.open("rb") as f:
            data = tomllib.load(f)
        daemon = data.get("daemon", {})
        if "host" in daemon:
            cfg.host = str(daemon["host"])
        if "port" in daemon:
            cfg.port = int(daemon["port"])
        if "memory_root" in daemon:
            cfg.memory_root = Path(str(daemon["memory_root"])).expanduser()
        if "auth_token" in daemon:
            cfg.auth_token = str(daemon["auth_token"]) or None
        projects_raw = data.get("projects", [])
        cfg.projects = [
            Project(
                slug=str(p["slug"]),
                path=Path(str(p["path"])).expanduser(),
                github_repo=str(p["github_repo"]) if p.get("github_repo") else None,
                primary=bool(p.get("primary", False)),
            )
            for p in projects_raw
        ]
        return cfg

    @classmethod
    def _apply_env(cls, cfg: AppConfig) -> AppConfig:
        root = os.environ.get("REACHY_DUCKY_MEMORY_ROOT")
        if root:
            cfg.memory_root = Path(root).expanduser()
        host = os.environ.get("REACHY_DUCKY_DAEMON_HOST")
        if host:
            cfg.host = host
        port = os.environ.get("REACHY_DUCKY_DAEMON_PORT")
        if port:
            cfg.port = int(port)
        token = os.environ.get("REACHY_DUCKY_AUTH_TOKEN")
        if token is not None:
            # Presence (even empty string) overrides TOML — an explicitly-empty
            # env var means "the user wants no token", which is different from
            # "the user didn't mention it".
            cfg.auth_token = token or None
        # Single-project env-var fallback (backward-compat with pre-6.4 shape).
        # Only engages when TOML supplied no [[projects]] — mixing is never
        # useful and would silently hide the TOML entries.
        proj_root = os.environ.get("REACHY_DUCKY_PROJECT_ROOT")
        if proj_root and not cfg.projects:
            slug = os.environ.get(
                "REACHY_DUCKY_PROJECT_SLUG",
                Path(proj_root).expanduser().name,
            )
            github_repo = os.environ.get("REACHY_DUCKY_GITHUB_REPO") or None
            cfg.projects = [
                Project(
                    slug=slug,
                    path=Path(proj_root).expanduser(),
                    github_repo=github_repo,
                    primary=True,
                )
            ]
        return cfg

    def warn_if_exposed_without_auth(self) -> None:
        """Log a loud warning if the daemon is bound off loopback with no token."""
        if self.host not in _LOOPBACK_HOSTS and self.auth_token is None:
            logger.warning(
                "reachy-ducky-daemon is bound to %s with NO auth token. "
                "Anyone on this network can invoke your Claude subscription and "
                "read your code. Set REACHY_DUCKY_AUTH_TOKEN, or bind to a "
                "Tailscale-only interface (recommended).",
                self.host,
            )
