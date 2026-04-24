"""Tests for the embodiment state machine + MockMotionDriver."""

from __future__ import annotations

import pytest
from reachy_ducky_app.embodiment import (
    EmbodimentStateMachine,
    MockMotionDriver,
    MotionDriver,
)
from reachy_ducky_app.mute import MuteGate
from reachy_ducky_protocol.messages import State


def test_motion_driver_is_abstract() -> None:
    """MotionDriver cannot be instantiated directly — it is an ABC."""
    with pytest.raises(TypeError):
        MotionDriver()  # type: ignore[abstract]


def test_initial_state_is_idle() -> None:
    """A freshly-constructed state machine starts in IDLE with no motion emitted."""
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver)
    assert sm.state == State.IDLE
    assert driver.moves == []
    assert driver.went_to_sleep is False
    assert driver.woke_up is False


def test_transition_to_listening_plays_move() -> None:
    """IDLE -> LISTENING plays the "listening" move."""
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver)
    sm.transition(State.LISTENING)
    assert driver.moves == ["listening"]
    assert sm.state == State.LISTENING


def test_transition_to_thinking_plays_move() -> None:
    """IDLE -> THINKING plays the "thinking" move."""
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver)
    sm.transition(State.THINKING)
    assert driver.moves == ["thinking"]
    assert sm.state == State.THINKING


def test_transition_to_idle_plays_neutral_move() -> None:
    """LISTENING -> IDLE plays the "neutral" move."""
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver)
    sm.transition(State.LISTENING)
    sm.transition(State.IDLE)
    assert driver.moves == ["listening", "neutral"]
    assert sm.state == State.IDLE


def test_transition_to_muted_goes_to_sleep() -> None:
    """LISTENING -> MUTED calls go_to_sleep and does NOT play a move for MUTED."""
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver)
    sm.transition(State.LISTENING)
    driver.moves.clear()  # isolate the MUTED transition
    sm.transition(State.MUTED)
    assert driver.went_to_sleep is True
    assert driver.moves == []
    assert sm.state == State.MUTED


def test_transition_from_muted_wakes_up_and_plays_target() -> None:
    """Exiting MUTED calls wake_up THEN play_move for the target state."""
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver)
    sm.transition(State.MUTED)
    assert driver.went_to_sleep is True
    assert driver.woke_up is False  # not yet

    sm.transition(State.LISTENING)
    # wake_up must fire before play_move so the robot is upright first.
    assert driver.woke_up is True
    assert driver.moves == ["listening"]
    assert sm.state == State.LISTENING


def test_same_state_transition_is_noop() -> None:
    """LISTENING -> LISTENING records no new calls."""
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver)
    sm.transition(State.LISTENING)
    assert driver.moves == ["listening"]

    sm.transition(State.LISTENING)
    assert driver.moves == ["listening"]  # unchanged
    assert driver.went_to_sleep is False
    assert driver.woke_up is False


def test_muted_to_muted_is_noop() -> None:
    """MUTED -> MUTED does NOT re-call go_to_sleep."""
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver)
    sm.transition(State.MUTED)
    # Force the flag back off to prove the second transition is a no-op
    # (rather than relying on idempotence of the bool flag itself).
    driver.went_to_sleep = False
    sm.transition(State.MUTED)
    assert driver.went_to_sleep is False
    assert sm.state == State.MUTED


def test_state_property_reflects_current_state() -> None:
    """After each transition, sm.state reflects the new state.

    mypy's literal-narrowing would otherwise flag consecutive equality
    checks as unreachable (it can't see that ``transition`` mutates
    ``_state``), so each assertion goes through a fresh local.
    """
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver)

    sm.transition(State.LISTENING)
    after_listening: State = sm.state
    assert after_listening == State.LISTENING

    sm.transition(State.THINKING)
    after_thinking: State = sm.state
    assert after_thinking == State.THINKING

    sm.transition(State.MUTED)
    after_muted: State = sm.state
    assert after_muted == State.MUTED


def test_sequence_idle_listening_thinking_muted_idle() -> None:
    """End-to-end sequence lockdown across every transition type."""
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver)

    sm.transition(State.LISTENING)
    sm.transition(State.THINKING)
    sm.transition(State.MUTED)
    sm.transition(State.IDLE)

    # Moves played in order: listening (on entry), thinking (mid), neutral
    # (after wake_up on exit from MUTED). MUTED itself does not play a move.
    assert driver.moves == ["listening", "thinking", "neutral"]
    assert driver.went_to_sleep is True
    assert driver.woke_up is True
    assert sm.state == State.IDLE


@pytest.mark.parametrize(
    "target",
    [State.IDLE, State.LISTENING, State.THINKING, State.MUTED],
)
def test_every_state_has_entry_handling(target: State) -> None:
    """Transitioning from IDLE to every State value must not raise."""
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver)
    sm.transition(target)
    assert sm.state == target


def test_transition_to_muted_sets_mute_gate() -> None:
    """Transitioning the state machine into MUTED toggles the MuteGate.

    Earliest-boundary mute coordination: downstream consumers (mic pump,
    VU meter, head wobble) all see the gate state change without each
    needing its own transition subscription.
    """
    gate = MuteGate()
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver, mute_gate=gate)

    assert gate.muted is False
    sm.transition(State.MUTED)
    assert gate.muted is True


def test_transition_out_of_muted_clears_mute_gate() -> None:
    """Leaving MUTED clears the gate symmetrically."""
    gate = MuteGate()
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver, mute_gate=gate)

    sm.transition(State.MUTED)
    assert gate.muted is True
    sm.transition(State.IDLE)
    assert gate.muted is False


def test_state_machine_without_mute_gate_still_works() -> None:
    """``mute_gate=None`` is back-compat: no gate calls; transitions still fire."""
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver)  # No mute_gate kwarg.
    sm.transition(State.MUTED)
    assert sm.state == State.MUTED
    # Driver still got the visible sleep posture (existing behaviour).
    assert driver.went_to_sleep is True


def test_failed_motion_leaves_state_and_mute_gate_unchanged() -> None:
    """If the driver raises, _state AND _mute_gate stay at prior values.

    Atomic-on-success contract: a half-failed transition (state moved,
    gate didn't, or vice versa) is the exact bug Codex/Augment surfaced
    on PR #71. Pinning here so a future refactor can't reintroduce it.
    """

    class _RaisingDriver(MotionDriver):
        def play_move(self, name: str) -> None:
            raise RuntimeError("simulated driver failure")

        def go_to_sleep(self) -> None:
            raise RuntimeError("simulated driver failure")

        def wake_up(self) -> None:
            raise RuntimeError("simulated driver failure")

        def look_at_image(self, u: float, v: float, duration: float = 0.3) -> None:
            raise RuntimeError("simulated driver failure")

    gate = MuteGate()
    driver = _RaisingDriver()
    sm = EmbodimentStateMachine(driver, mute_gate=gate)

    # MUTED entry that fails: state and gate must both stay at IDLE / unmuted.
    with pytest.raises(RuntimeError, match="simulated driver failure"):
        sm.transition(State.MUTED)
    assert sm.state == State.IDLE, "state advanced despite driver failure"
    assert gate.muted is False, "gate flipped despite driver failure"


def test_failed_motion_from_muted_leaves_state_and_mute_gate_unchanged() -> None:
    """Symmetric inverse: failed exit from MUTED keeps state == MUTED AND
    gate.muted == True.

    Codex specifically called out this direction (mic stays zeroed even
    though the app reports muted — but state ALSO stays muted, so no
    desync; the failed transition is a no-op for both).
    """

    class _ConditionalDriver(MotionDriver):
        """Succeeds on go_to_sleep (entering MUTED), then raises on wake_up."""

        def __init__(self) -> None:
            self.went_to_sleep = False

        def play_move(self, name: str) -> None:
            pass  # not exercised in this test

        def go_to_sleep(self) -> None:
            self.went_to_sleep = True

        def wake_up(self) -> None:
            raise RuntimeError("simulated wake_up failure")

        def look_at_image(self, u: float, v: float, duration: float = 0.3) -> None:
            pass  # not exercised in this test

    gate = MuteGate()
    driver = _ConditionalDriver()
    sm = EmbodimentStateMachine(driver, mute_gate=gate)

    # Enter MUTED — succeeds.
    sm.transition(State.MUTED)
    assert sm.state == State.MUTED
    assert gate.muted is True

    # Exit MUTED — wake_up fails. State + gate must stay at MUTED / muted.
    with pytest.raises(RuntimeError, match="simulated wake_up failure"):
        sm.transition(State.IDLE)
    assert sm.state == State.MUTED, "state advanced despite wake_up failure"
    assert gate.muted is True, "gate cleared despite wake_up failure"
