"""Embodiment state machine: maps :class:`State` transitions to motion."""

from __future__ import annotations

from reachy_ducky_protocol.messages import State

from .motion_driver import MotionDriver

# State -> emotion-library move name. Keep in sync with the design doc §11
# (Embodiment) which lists the canonical moves.
_STATE_TO_MOVE: dict[State, str] = {
    State.IDLE: "neutral",
    State.LISTENING: "listening",
    State.THINKING: "thinking",
}


class EmbodimentStateMachine:
    """Maps :class:`State` transitions to motion commands.

    Invariants:

    - A transition to the SAME state is a no-op (no redundant motion).
    - ``MUTED`` is special-cased to :meth:`MotionDriver.go_to_sleep` (visible
      "off" posture) rather than ``play_move``.
    - Exiting ``MUTED`` triggers :meth:`MotionDriver.wake_up` BEFORE the
      target-state ``play_move`` so the robot is upright before it moves.
    - ``IDLE``/``LISTENING``/``THINKING`` transitions call
      ``play_move(<name>)`` per :data:`_STATE_TO_MOVE`.
    """

    def __init__(self, driver: MotionDriver) -> None:
        self._driver = driver
        self._state: State = State.IDLE

    @property
    def state(self) -> State:
        return self._state

    def transition(self, target: State) -> None:
        if target == self._state:
            return
        if target == State.MUTED:
            self._driver.go_to_sleep()
        else:
            if self._state == State.MUTED:
                self._driver.wake_up()
            move = _STATE_TO_MOVE.get(target)
            if move is not None:
                self._driver.play_move(move)
        self._state = target
