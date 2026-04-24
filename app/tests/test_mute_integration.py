"""Integration smoke — state machine -> MuteGate -> ReachyMicSource.

Proves the A.1 + A.2 wiring end-to-end without hardware. Uses real
``EmbodimentStateMachine``, ``MuteGate``, and ``ReachyMicSource``
instances; only the underlying SDK is mocked (via a scripted
``_ScriptedFakeMini``). A real hardware equivalent is filed in
``test_main_hardware.py`` and runs under ``@pytest.mark.hardware``.

This is NOT a unit test — it deliberately crosses module boundaries.
Lives in its own file so it's easy to find and doesn't inflate the
individual ``test_*.py`` files.
"""

from __future__ import annotations

import asyncio
import contextlib

import numpy as np
from reachy_ducky_app.embodiment import EmbodimentStateMachine, MockMotionDriver
from reachy_ducky_app.mute import MuteGate
from reachy_ducky_app.voice.audio_io import AudioFrame, ReachyMicSource
from reachy_ducky_protocol.messages import State


class _ScriptedFakeMini:
    """FakeMini whose ``get_audio_sample`` is driven by a queue.

    A real mic returns frames at sample-rate-bounded cadence; a list-pop
    fake returns them as fast as the executor can call it, which lets
    the mic pump outrun any orchestrator coroutine in the same task
    group. Wrapping the queue lets the orchestrator deterministically
    feed exactly one frame at a time and observe the resulting yield.

    ``get_audio_sample`` returns ``None`` (the SDK's "buffer empty"
    signal) when the queue is empty, which the mic loop handles with
    its 10 ms sleep — natural pacing.
    """

    def __init__(self) -> None:
        self._pending: list[np.ndarray] = []
        self.media = self._Media(self)

    def push(self, frame: np.ndarray) -> None:
        self._pending.append(frame)

    class _Media:
        def __init__(self, outer: _ScriptedFakeMini) -> None:
            self._outer = outer

        def get_audio_sample(self) -> np.ndarray | None:
            if not self._outer._pending:
                return None
            return self._outer._pending.pop(0)


async def _await_frame_count(
    frames: list[AudioFrame], target: int, *, timeout: float = 2.0
) -> None:
    """Poll until ``frames`` reaches ``target`` length or timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while len(frames) < target:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for frame {target}; got {len(frames)}")
        await asyncio.sleep(0.01)


async def test_state_machine_transition_to_muted_zeros_mic_frames() -> None:
    """Transitioning the state machine to MUTED zeros subsequent mic frames.

    Drives: sm.transition(MUTED) -> gate.set_muted(True) -> mic.frames()
    yields zeros. Reverses: sm.transition(IDLE) -> gate clears ->
    subsequent frames non-zero.
    """
    mini = _ScriptedFakeMini()
    gate = MuteGate()
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver, mute_gate=gate)
    src = ReachyMicSource(mini, mute_gate=gate)

    frames_generated: list[AudioFrame] = []

    async def drive_mic() -> None:
        with contextlib.suppress(asyncio.CancelledError):
            async for frame in src.frames():
                frames_generated.append(frame)

    mic_task = asyncio.create_task(drive_mic())
    try:
        # Frame 1: unmuted IDLE.
        mini.push(np.full((480, 2), 0.5, dtype=np.float32))
        await _await_frame_count(frames_generated, 1)

        # Frame 2: muted.
        sm.transition(State.MUTED)
        mini.push(np.full((480, 2), 0.5, dtype=np.float32))
        await _await_frame_count(frames_generated, 2)

        # Frame 3: still muted.
        mini.push(np.full((480, 2), 0.5, dtype=np.float32))
        await _await_frame_count(frames_generated, 3)

        # Frame 4: unmuted again.
        sm.transition(State.IDLE)
        mini.push(np.full((480, 2), 0.5, dtype=np.float32))
        await _await_frame_count(frames_generated, 4)
    finally:
        mic_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await mic_task

    assert len(frames_generated) == 4

    # Frame 1: captured BEFORE any transition — non-zero.
    assert np.any(frames_generated[0][1] != 0), "pre-mute frame was zeroed"
    # Frames 2 + 3: AFTER transition to MUTED — zeroed.
    assert np.all(frames_generated[1][1] == 0), "muted frame 2 had signal"
    assert np.all(frames_generated[2][1] == 0), "muted frame 3 had signal"
    # Frame 4: AFTER transition back to IDLE — non-zero again.
    assert np.any(frames_generated[3][1] != 0), "post-unmute frame was zeroed"


async def test_mute_gate_shared_between_state_machine_and_mic_source() -> None:
    """Single MuteGate instance threaded to both ends — identity property."""
    gate = MuteGate()
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver, mute_gate=gate)

    mini = _ScriptedFakeMini()
    src = ReachyMicSource(mini, mute_gate=gate)

    bound_gate = src._mute_gate  # noqa: SLF001 — integration smoke.
    assert bound_gate is not None
    assert bound_gate is gate
    assert bound_gate.muted is False

    sm.transition(State.MUTED)
    assert bound_gate.muted is True  # same object as ``gate``.
