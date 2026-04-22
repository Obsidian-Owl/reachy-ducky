"""Sim-integration tests for :class:`ReachyMotionDriver`.

These tests exercise the real ``reachy_mini.ReachyMini`` SDK against a live
``reachy-mini-daemon --sim`` on ``localhost:8000``. They are gated behind
``@pytest.mark.sim`` and collected only when ``pytest -m sim`` is passed.

They exist to catch SDK signature drift that the mocked unit tier cannot
see: ``ReachyMotionDriver`` carries six ``# type: ignore[attr-defined]``
suppressions (see issue #18) because the ``reachy_mini`` wheel ships no
``py.typed`` marker. If the SDK renames a method (e.g. ``goto_sleep`` →
``go_to_sleep``), these tests fail where the unit tier stays green.

Scope: motion + lifecycle only. Audio/mic/speaker/wake are hardware-tier.

No assertions are made on motion *outcomes* (joint positions, image
pixels): the sim's behaviour is not deterministic at that granularity and
such assertions flake. Each test asserts that the SDK call completes
without raising — sufficient to detect signature drift, which is the
whole point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from reachy_ducky_app.embodiment import EmbodimentStateMachine
from reachy_ducky_app.embodiment.motion_driver import ReachyMotionDriver
from reachy_ducky_protocol.messages import State

if TYPE_CHECKING:
    from collections.abc import Iterator

# Everything in this module requires ``-m sim``. Marking at module level
# keeps individual tests tidy and matches how ``@pytest.mark.integration``
# is applied elsewhere in this repo.
pytestmark = pytest.mark.sim


_DAEMON_STATUS_URL = "http://localhost:8000/api/daemon/status"
_DAEMON_STARTUP_MSG = (
    "reachy-mini-daemon --sim is not reachable at localhost:8000. "
    "Start it with:\n"
    "    pip install 'reachy-mini[mujoco]'\n"
    "    reachy-mini-daemon --robot-name reachy_mini_sim --sim --headless --localhost-only"
)


def _daemon_reachable() -> bool:
    """Return ``True`` iff the daemon answers on ``localhost:8000``.

    We deliberately do not parse the response body — any successful
    HTTP response proves the daemon is bound and serving. The sim
    daemon may spend a few hundred ms in ``starting`` before reaching
    ``running``; that is fine for test purposes because the SDK's
    client constructor blocks until ready.
    """
    try:
        # Fixed localhost URL, not user-controlled — S310/B310 suppression
        # is safe. ``# noqa`` is ruff's; ``# nosec`` is bandit's.
        with urlopen(_DAEMON_STATUS_URL, timeout=2.0) as resp:  # noqa: S310  # nosec B310
            status: int = resp.status
            return 200 <= status < 300
    except (URLError, OSError, TimeoutError):
        return False


@pytest.fixture(scope="session", autouse=True)
def _require_sim_daemon() -> None:
    """Fail loudly if the sim daemon isn't reachable.

    Per ``testing-standards.md`` we never ``skip`` tests that describe
    real behaviour. If the user opts into ``-m sim`` without a live
    daemon, the expectation is a clear error — not a silent skip.
    """
    if not _daemon_reachable():
        pytest.fail(_DAEMON_STARTUP_MSG, pytrace=False)


@pytest.fixture
def mini() -> Iterator[object]:
    """Construct a real ``ReachyMini`` client and clean it up afterwards.

    Importing ``reachy_mini`` is deferred to this fixture so that the
    test module collects cleanly on machines without the SDK — the
    session-level fixture above will have already failed with a clear
    "start the daemon" message before we get here.
    """
    try:
        from reachy_mini import ReachyMini  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised via the failure path only
        pytest.fail(
            "reachy_mini SDK is not installed. Install with "
            "`pip install 'reachy-mini[mujoco]'` (or `uv sync --extra robot`) "
            f"then retry. Import error: {exc}",
            pytrace=False,
        )

    client = ReachyMini()
    try:
        yield client
    finally:
        # Best-effort close. Newer SDK versions expose ``close()``; older
        # ones rely on garbage-collection. Catch AttributeError so we
        # don't mask real test failures.
        close = getattr(client, "close", None)
        if callable(close):
            close()


def test_driver_connects_to_sim_daemon(mini: object) -> None:
    """Wrapping a live ``ReachyMini`` must not raise.

    Side-effect proof: the ``ReachyMini`` fixture construction above
    connects to ``localhost:8000`` — if the daemon weren't serving, the
    SDK's constructor would raise and the test would fail before this
    body runs.
    """
    driver = ReachyMotionDriver(mini)
    assert driver is not None


def test_driver_wake_up_goto_sleep_cycle(mini: object) -> None:
    """``wake_up`` and ``go_to_sleep`` must round-trip against the real SDK.

    This is the test that catches SDK renames (issue #18). The unit
    tier's ``MockMotionDriver`` never sees the actual SDK surface, so
    a rename like ``goto_sleep`` → ``go_to_sleep`` would pass unit CI
    but break at runtime.
    """
    driver = ReachyMotionDriver(mini)
    driver.wake_up()
    driver.go_to_sleep()


@pytest.mark.parametrize("move_name", ["neutral", "listening"])
def test_driver_play_move_accepts_canonical_names(mini: object, move_name: str) -> None:
    """Canonical move names from ``_STATE_TO_MOVE`` must be accepted by the SDK.

    If the sim rejects either name, stop and escalate — do NOT silently
    substitute a different name. The state machine's contract says
    these moves exist, and drift between the state-machine map and the
    SDK's move library is a real finding worth surfacing.
    """
    driver = ReachyMotionDriver(mini)
    driver.wake_up()  # moves only work from an awake posture
    driver.play_move(move_name)


def test_driver_look_at_image_accepts_pixel_coords(mini: object) -> None:
    """``look_at_image(u, v)`` must accept integer pixel coordinates.

    Our gaze loop feeds pixel coordinates derived from MediaPipe face
    detection. This test asserts the SDK accepts a reasonable pair
    without raising — not that the head points anywhere in particular.
    """
    driver = ReachyMotionDriver(mini)
    driver.wake_up()
    driver.look_at_image(320.0, 240.0)


def test_embodiment_state_cycle_drives_real_sdk(mini: object) -> None:
    """End-to-end: ``EmbodimentStateMachine`` driving the real SDK must not raise.

    Walks ``IDLE → LISTENING → THINKING → MUTED → IDLE`` — the canonical
    lifecycle from ``docs/plans/2026-04-21-reachy-ducky-design.md §11``.
    Each transition exercises a different SDK method (``play_move``,
    ``go_to_sleep``, ``wake_up``), covering the full signature surface
    in one test.
    """
    driver = ReachyMotionDriver(mini)
    driver.wake_up()  # start from a known awake posture
    sm = EmbodimentStateMachine(driver=driver)

    # Fresh ``State`` locals after each transition prevent mypy's
    # literal-narrowing from flagging later asserts as unreachable —
    # same pattern used in ``test_state_property_reflects_current_state``.
    sm.transition(State.LISTENING)
    after_listening: State = sm.state
    assert after_listening == State.LISTENING

    sm.transition(State.THINKING)
    after_thinking: State = sm.state
    assert after_thinking == State.THINKING

    sm.transition(State.MUTED)
    after_muted: State = sm.state
    assert after_muted == State.MUTED

    sm.transition(State.IDLE)
    after_idle: State = sm.state
    assert after_idle == State.IDLE
