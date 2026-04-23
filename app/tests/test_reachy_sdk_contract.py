"""Contract tests for the reachy-mini SDK surface we depend on.

Purpose
-------
``ReachyMotionDriver`` calls six methods on a ``ReachyMini`` instance
(``play_move``, ``go_to_sleep`` / ``goto_sleep``, ``wake_up``,
``look_at_image``). The SDK ships no ``py.typed`` marker, so the
calls are silenced with ``# type: ignore[attr-defined]`` — if a
future SDK release renames a method, the mocked unit tier stays
green and the first real-hardware user hits ``AttributeError``.

These tests catch that drift by inspecting the real ``ReachyMini``
class surface (no instance construction; no daemon required; no
MuJoCo or GStreamer) and asserting our required method names are
still present. Since reachy-mini is now a plain base dep, these run
in the default unit tier on every machine.

Why introspection instead of running against a sim daemon
---------------------------------------------------------
The behaviour-level sim CI we attempted in PR #39 required:

- libcairo/libgirepository/libglib dev headers
- GStreamer core + base-plugins GI typelibs
- MuJoCo EGL / OSMesa / GLFW render deps

Even with all of those installed, the daemon booted but
``ReachyMini()`` construction still failed because the client
eagerly initialises a WebRTC media pipeline that sim-localhost
doesn't expose. Chasing Pollen's private-runner topology on
GitHub's free ubuntu-latest turned out to be a losing trade.

The class-surface introspection test gives us ~90% of the original
value (catching SDK renames) at ~5% of the maintenance cost. Full
behaviour verification lives on real hardware where it belongs.
"""

from __future__ import annotations

import pytest

# These are the exact method names ``ReachyMotionDriver`` calls. Keep
# in sync with ``app/src/reachy_ducky_app/embodiment/motion_driver.py``.
# If you add a driver method that calls a new SDK method, add it here.
_REQUIRED_METHODS = (
    "play_move",
    "goto_sleep",
    "wake_up",
    "look_at_image",
)


@pytest.mark.parametrize("method_name", _REQUIRED_METHODS)
def test_reachy_mini_class_exposes_required_method(method_name: str) -> None:
    """Each ``ReachyMotionDriver``-consumed method exists on ``ReachyMini``.

    A bare ``hasattr`` / ``getattr`` check is enough to catch renames:
    we don't care about signatures yet (the call sites use positional
    args only and the SDK is kwargs-permissive).

    Side-effect proof: the ``hasattr`` consults the class's attribute
    table, which reflects the actual installed SDK version's surface.
    """
    # reachy-mini ships no `py.typed` marker; narrow mypy ignore at the
    # single import site avoids a blanket per-module override.
    from reachy_mini import ReachyMini  # type: ignore[import-untyped]

    assert hasattr(ReachyMini, method_name), (
        f"reachy-mini SDK missing `{method_name}`. "
        f"ReachyMotionDriver calls this; check the upstream changelog "
        f"for a rename and update both the driver and this test."
    )
    assert callable(getattr(ReachyMini, method_name)), (
        f"`ReachyMini.{method_name}` exists but is not callable — "
        f"the SDK may have turned it into a property or descriptor."
    )
