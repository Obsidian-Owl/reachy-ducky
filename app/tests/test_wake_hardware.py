"""Hardware smoke for #55 — real openWakeWord detection on the Reachy Mini.

Gated on ``@pytest.mark.hardware``. Runs only with a Reachy Mini Wireless
reachable on the LAN AND a human in the loop ready to say the wake word.
Invoke with ``uv run pytest -m hardware -v``.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

reachy_mini = pytest.importorskip(
    "reachy_mini",
    reason="hardware tests require the reachy-mini SDK installed",
)


@pytest.mark.hardware
@pytest.mark.asyncio
async def test_open_wake_word_fires_on_real_utterance() -> None:
    """Human says 'hey jarvis' within 10s; wake.event fires exactly once.

    Human-in-the-loop. The test prints a clear instruction at the start;
    a human listener must say the wake phrase. If the test times out
    without a fire, either the human didn't speak, the mic is dead, or
    the model genuinely missed — log inspection separates these.
    """
    from reachy_ducky_app.voice.audio_io import ReachyMicSource
    from reachy_ducky_app.wake_onnx import OpenWakeWordDetector

    print("\n>>> SAY 'hey jarvis' WITHIN 10 SECONDS <<<\n", flush=True)

    with reachy_mini.ReachyMini() as mini:
        mic = ReachyMicSource(mini)
        det = OpenWakeWordDetector.from_vendored_weights()

        async def pump() -> None:
            async for frame in mic.frames():
                det.feed(frame)
                if det.event.is_set():
                    return

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(pump(), timeout=10.0)

        assert det.event.is_set(), (
            "wake.event did not fire after 10s — did you say 'hey jarvis'? "
            "Check mic frames are flowing (test_sdk_audio_contract hardware tier "
            "should be green) and that the model file is present."
        )


@pytest.mark.hardware
@pytest.mark.asyncio
async def test_open_wake_word_paused_during_turn_phase() -> None:
    """After detection + reset, no further fires until the next utterance.

    Pin: once detected and reset, the detector does NOT spuriously fire
    on ambient room audio for at least 5s. Human-in-the-loop: stay quiet.
    """
    from reachy_ducky_app.voice.audio_io import ReachyMicSource
    from reachy_ducky_app.wake_onnx import OpenWakeWordDetector

    print("\n>>> SAY 'hey jarvis' WITHIN 10 SECONDS, THEN STAY QUIET <<<\n", flush=True)

    with reachy_mini.ReachyMini() as mini:
        mic = ReachyMicSource(mini)
        det = OpenWakeWordDetector.from_vendored_weights()

        async def first_fire() -> None:
            async for frame in mic.frames():
                det.feed(frame)
                if det.event.is_set():
                    return

        await asyncio.wait_for(first_fire(), timeout=10.0)
        assert det.event.is_set()
        det.reset()

        feed_calls = 0

        async def quiet_window() -> None:
            nonlocal feed_calls
            async for frame in mic.frames():
                feed_calls += 1
                det.feed(frame)
                if det.event.is_set():
                    return

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(quiet_window(), timeout=5.0)

        assert feed_calls > 0, "mic produced no frames in the quiet window"
        assert not det.event.is_set(), (
            "wake.event fired spuriously during the quiet window — false-positive on ambient audio"
        )
