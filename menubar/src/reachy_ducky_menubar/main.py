"""Rumps-based Mac menu-bar app for Reachy Ducky.

Polls the daemon's /health every 2s. Icon reflects local state
(idle <-> muted). Daemon unreachable -> warning glyph + status message.

macOS-only: ``rumps`` is an optional ``macos`` extra. Importing this
module on non-Darwin stays cheap (no rumps import at module scope);
``main()`` raises ``ImportError`` before it would try to import rumps,
so tests and Linux CI don't blow up.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

import httpx
from reachy_ducky_protocol.messages import State

from .state_icon import icon_for

_DAEMON_URL_DEFAULT = "http://127.0.0.1:8765"
_POLL_INTERVAL_SECONDS = 2.0
_UNREACHABLE_GLYPH = "🦆⚠"


def main() -> None:
    """Start the menubar app. Raises ``ImportError`` on non-Darwin platforms."""
    if sys.platform != "darwin":
        raise ImportError(
            "reachy-ducky-menubar is macOS-only (requires rumps). "
            "Install on a Mac with `uv sync --all-packages --extra macos`."
        )
    # Lazy import so non-Darwin CI can import this module without rumps.
    # `rumps` ships no type stubs; it's only installed in the `macos` extra,
    # hence the import-not-found ignore.
    import rumps  # type: ignore[import-not-found]

    app = _build_app(rumps)
    app.run()


def _build_app(rumps_mod: Any, *, start_poll: bool = True) -> Any:
    """Construct the ``rumps.App`` with state, menu, and polling thread.

    Factored out so tests can construct (without running) by passing a fake
    ``rumps`` module. ``start_poll=False`` skips the background thread, which
    keeps unit tests deterministic.
    """
    # Local state — mute is menubar-controlled.
    state: dict[str, State] = {"current": State.IDLE}
    daemon_url = _DAEMON_URL_DEFAULT

    app = rumps_mod.App(icon_for(State.IDLE), quit_button="Quit")
    mute_item = rumps_mod.MenuItem("Mute")
    status_item = rumps_mod.MenuItem(f"Status: {State.IDLE.value}")
    app.menu = [status_item, mute_item]

    def _set_state(new: State) -> None:
        state["current"] = new
        app.title = icon_for(new)
        status_item.title = f"Status: {new.value}"

    def _toggle_mute(_item: Any) -> None:
        if state["current"] == State.MUTED:
            _set_state(State.IDLE)
            mute_item.state = 0
        else:
            _set_state(State.MUTED)
            mute_item.state = 1

    mute_item.set_callback(_toggle_mute)

    def _poll_loop() -> None:
        while True:
            if state["current"] == State.MUTED:
                time.sleep(_POLL_INTERVAL_SECONDS)
                continue
            try:
                r = httpx.get(f"{daemon_url}/health", timeout=1.0)
                if r.is_success:
                    status_item.title = f"Status: {state['current'].value}"
                    app.title = icon_for(state["current"])
                else:
                    app.title = _UNREACHABLE_GLYPH
                    status_item.title = f"Status: daemon error ({r.status_code})"
            except httpx.HTTPError:
                app.title = _UNREACHABLE_GLYPH
                status_item.title = "Status: daemon unreachable"
            time.sleep(_POLL_INTERVAL_SECONDS)

    if start_poll:
        threading.Thread(target=_poll_loop, daemon=True).start()
    return app
