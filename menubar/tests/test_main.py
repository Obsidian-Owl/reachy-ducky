"""Cross-platform tests for ``reachy_ducky_menubar.main``.

Uses a hand-rolled fake ``rumps`` module so we can exercise the menubar
app's construction + mute toggle on Linux CI without installing rumps.
"""

from __future__ import annotations

from typing import Any

import pytest
from reachy_ducky_menubar.main import _UNREACHABLE_GLYPH, _build_app, main
from reachy_ducky_menubar.state_icon import icon_for
from reachy_ducky_protocol.messages import State


class _FakeApp:
    """Stand-in for ``rumps.App`` — stores title + menu, nothing else."""

    def __init__(self, title: str, quit_button: str | None = None) -> None:
        self.title = title
        self.quit_button = quit_button
        self.menu: list[Any] | None = None


class _FakeMenuItem:
    """Stand-in for ``rumps.MenuItem`` — captures callback + state."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.state = 0
        self._callback: Any = None

    def set_callback(self, cb: Any) -> None:
        self._callback = cb


class _FakeRumps:
    """Minimal ``rumps`` module surface used by ``_build_app``."""

    App = _FakeApp
    MenuItem = _FakeMenuItem


def _find_mute_item(app: _FakeApp) -> _FakeMenuItem:
    """Locate the Mute menu item on a fake app, failing loudly if absent."""
    assert app.menu is not None, "menu was never assigned"
    for item in app.menu:
        if isinstance(item, _FakeMenuItem) and item.title == "Mute":
            return item
    raise AssertionError("Mute menu item missing from app.menu")


def _find_status_item(app: _FakeApp) -> _FakeMenuItem:
    """Locate the Status menu item (its title starts with 'Status: ')."""
    assert app.menu is not None, "menu was never assigned"
    for item in app.menu:
        if isinstance(item, _FakeMenuItem) and item.title.startswith("Status: "):
            return item
    raise AssertionError("Status menu item missing from app.menu")


def test_main_raises_import_error_on_non_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    """On non-Darwin platforms, ``main()`` refuses to import rumps."""
    monkeypatch.setattr("sys.platform", "linux")
    with pytest.raises(ImportError, match="macOS-only"):
        main()


def test_build_app_initial_state_is_idle() -> None:
    """_build_app starts with IDLE icon and a 'Status: idle' menu entry."""
    app = _build_app(_FakeRumps(), start_poll=False)
    assert isinstance(app, _FakeApp)
    assert app.title == icon_for(State.IDLE)
    status = _find_status_item(app)
    assert status.title == f"Status: {State.IDLE.value}"


def test_build_app_menu_contains_status_and_mute_items() -> None:
    """The menu wires up both the status entry and the mute toggle."""
    app = _build_app(_FakeRumps(), start_poll=False)
    assert isinstance(app, _FakeApp)
    mute = _find_mute_item(app)
    status = _find_status_item(app)
    assert mute.title == "Mute"
    assert status.title.startswith("Status: ")


def test_mute_callback_toggles_to_muted_then_back_to_idle() -> None:
    """Hitting the mute item flips state MUTED↔IDLE and updates the icon."""
    app = _build_app(_FakeRumps(), start_poll=False)
    assert isinstance(app, _FakeApp)
    mute = _find_mute_item(app)
    status = _find_status_item(app)

    # First click: IDLE → MUTED.
    assert mute._callback is not None, "mute callback was never registered"
    mute._callback(mute)
    assert app.title == icon_for(State.MUTED)
    assert status.title == f"Status: {State.MUTED.value}"
    assert mute.state == 1

    # Second click: MUTED → IDLE.
    mute._callback(mute)
    assert app.title == icon_for(State.IDLE)
    assert status.title == f"Status: {State.IDLE.value}"
    assert mute.state == 0


def test_unreachable_glyph_is_distinct_from_state_icons() -> None:
    """The daemon-unreachable glyph must not collide with any state icon."""
    state_icons = {icon_for(s) for s in State}
    assert _UNREACHABLE_GLYPH not in state_icons
