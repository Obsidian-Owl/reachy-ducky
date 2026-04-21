"""Env-driven daemon configuration."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass
class Config:
    memory_root: Path
    host: str = "127.0.0.1"
    port: int = 8765
    auth_token: str | None = None

    @classmethod
    def from_env(cls) -> Config:
        root = Path(
            os.environ.get(
                "REACHY_DUCKY_MEMORY_ROOT",
                str(Path.home() / ".reachy-ducky" / "memory"),
            )
        )
        host = os.environ.get("REACHY_DUCKY_DAEMON_HOST", "127.0.0.1")
        port = int(os.environ.get("REACHY_DUCKY_DAEMON_PORT", "8765"))
        token = os.environ.get("REACHY_DUCKY_AUTH_TOKEN") or None
        return cls(memory_root=root, host=host, port=port, auth_token=token)

    def warn_if_exposed_without_auth(self) -> None:
        """Print a loud warning if the daemon is bound off loopback with no token."""
        if self.host not in _LOOPBACK_HOSTS and self.auth_token is None:
            logger.warning(
                "reachy-ducky-daemon is bound to %s with NO auth token. "
                "Anyone on this network can invoke your Claude subscription and "
                "read your code. Set REACHY_DUCKY_AUTH_TOKEN, or bind to a "
                "Tailscale-only interface (recommended).",
                self.host,
            )
