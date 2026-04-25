"""Hardware smoke for #20 — mute coordination end-to-end on real hardware.

Gated on ``@pytest.mark.hardware``. Runs when the user is on LAN and
the Reachy Mini is reachable at ``reachy-mini.local``. Deferred from
the offline workstream (which landed the integration smoke in
``test_mute_integration.py``); runs alongside Task 1.5's audio smoke
when the user is back on LAN.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest
from reachy_ducky_app.embodiment import EmbodimentStateMachine, MockMotionDriver
from reachy_ducky_app.mute import MuteGate
from reachy_ducky_app.voice.audio_io import AudioFrame, ReachyMicSource
from reachy_ducky_protocol.messages import State

reachy_mini = pytest.importorskip(
    "reachy_mini",
    reason="hardware tests require the reachy-mini SDK installed",
)


@pytest.mark.hardware
async def test_muted_transition_zeros_live_mic() -> None:
    """On real hardware, MUTED transition yields zeroed int16 samples.

    Same contract as ``test_state_machine_transition_to_muted_zeros_mic_frames``
    in the offline integration smoke, but against a LIVE ReachyMini
    via the real SDK.
    """
    # Use the context manager so ``media_manager.close()`` runs on exit —
    # otherwise the GStreamer/WebRTC pipeline must be torn down by GC at
    # interpreter shutdown, which deadlocks on a tokio mutex inside
    # libgstrswebrtc and hangs pytest indefinitely.
    with reachy_mini.ReachyMini() as mini:
        gate = MuteGate()
        driver = MockMotionDriver()  # Don't actually care about motion side-effects here.
        sm = EmbodimentStateMachine(driver, mute_gate=gate)
        src = ReachyMicSource(mini, mute_gate=gate)

        # Pull a few frames pre-mute; just confirm the pipeline is alive.
        # Don't suppress CancelledError — wait_for delivers the timeout via
        # cancellation, and swallowing it would convert a "no frames in 5s"
        # timeout into a silent successful return. Length asserts after
        # each drain pin the actual frame count regardless.
        frames_before: list[AudioFrame] = []

        async def drain_pre() -> None:
            async for frame in src.frames():
                frames_before.append(frame)
                if len(frames_before) >= 2:
                    return

        await asyncio.wait_for(drain_pre(), timeout=5.0)
        assert len(frames_before) >= 2, (
            f"expected at least 2 pre-mute frames in 5s, got {len(frames_before)}"
        )

        # Transition to MUTED; subsequent frames must be all zeros.
        sm.transition(State.MUTED)
        frames_after: list[AudioFrame] = []

        async def drain_post() -> None:
            async for frame in src.frames():
                frames_after.append(frame)
                if len(frames_after) >= 2:
                    return

        await asyncio.wait_for(drain_post(), timeout=5.0)
        assert len(frames_after) >= 2, (
            f"expected at least 2 post-mute frames in 5s, got {len(frames_after)}"
        )

        for _sr, samples in frames_after:
            assert np.all(samples == 0), "live-hardware muted frame had signal"
