"""Integration tests: Pattern C lifecycle handoff in main._run_async.

Exercises the wake-pump → turn → restart cycle with fakes only — no
hardware, no ONNX, no real OpenAI. Pins the invariant that exactly
one mic consumer is active at any moment, and that wake.feed is paused
during a turn.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import pytest
from reachy_ducky_app.embodiment import EmbodimentStateMachine
from reachy_ducky_app.main import ReachyDuckyApp
from reachy_ducky_app.voice.audio_io import AudioFrame
from reachy_ducky_app.wake import MockWakeDetector
from reachy_ducky_protocol.messages import State


class _CountingMicSource:
    """Async mic source that counts how many times ``frames()`` was entered."""

    def __init__(self) -> None:
        self.entry_count = 0
        self.frames_yielded = 0

    async def frames(self) -> AsyncIterator[AudioFrame]:
        self.entry_count += 1
        try:
            while True:
                self.frames_yielded += 1
                yield (24_000, np.zeros(960, dtype=np.int16))
                await asyncio.sleep(0)  # cooperative yield
        finally:
            pass


class _FireOnceWake(MockWakeDetector):
    """Fires on the first feed call, then stays silent until reset()."""

    def __init__(self) -> None:
        super().__init__()
        self.feed_calls = 0
        self.reset_calls = 0
        self._fired = False

    def feed(self, frame: AudioFrame) -> None:
        self.feed_calls += 1
        if not self._fired:
            self._fired = True
            self.event.set()

    def reset(self) -> None:
        super().reset()
        self.reset_calls += 1
        self._fired = False


@pytest.mark.asyncio
async def test_wake_pump_fires_then_yields_to_turn_then_restarts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mic = _CountingMicSource()
    wake = _FireOnceWake()
    sm_transitions: list[State] = []

    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_mic_source",
        lambda **_: mic,
    )
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_speaker_sink",
        lambda **_: object(),
    )
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_wake_detector",
        lambda: wake,
    )

    fake_voice_calls: list[str] = []

    class _FakeVoice:
        def __init__(self, **_: Any) -> None: ...

    monkeypatch.setattr("reachy_ducky_app.main.OpenAIRealtimeVoice", _FakeVoice)

    fake_daemon = type(
        "FakeDaemon",
        (),
        {
            "from_env": classmethod(lambda cls: cls()),
            "aclose": staticmethod(lambda: asyncio.sleep(0)),
        },
    )()
    monkeypatch.setattr("reachy_ducky_app.main.DaemonClient", type(fake_daemon))

    # Snapshot wake.feed_calls at the START and END of each fake turn.
    # The invariant is that these are EQUAL — wake.feed must not run
    # during run_one_turn (Phase 2 == voice owns the mic exclusively).
    turn_feed_call_pairs: list[tuple[int, int]] = []

    async def _fake_run_one_turn(**_: Any) -> None:
        start_calls = wake.feed_calls
        # Yield once so the wake pump task (if it were buggily still
        # alive) would have a chance to run. Without this await, a stale
        # pump task wouldn't get scheduling time and the assertion below
        # would pass even on a real bug.
        await asyncio.sleep(0)
        end_calls = wake.feed_calls
        turn_feed_call_pairs.append((start_calls, end_calls))
        fake_voice_calls.append("turn")

    monkeypatch.setattr("reachy_ducky_app.main.run_one_turn", _fake_run_one_turn)

    # Capture state machine transitions
    def _capture_transition(self: Any, target: State) -> None:
        sm_transitions.append(target)

    monkeypatch.setattr(EmbodimentStateMachine, "transition", _capture_transition)

    stop_event = threading.Event()
    app = ReachyDuckyApp()

    async def _stop_after_two_turns() -> None:
        # Wait until at least two turns ran, then signal stop
        deadline = asyncio.get_event_loop().time() + 2.0
        while len(fake_voice_calls) < 2 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.01)
        stop_event.set()

    asyncio.create_task(_stop_after_two_turns())
    await app._run_async(reachy_mini=object(), stop_event=stop_event)

    # Pin the lifecycle invariants
    assert len(fake_voice_calls) >= 2, "expected at least 2 turns"
    assert mic.entry_count >= 2, "mic.frames() should be re-entered each phase 1"
    assert wake.reset_calls >= 2, "wake.reset() called once per phase 1 entry"
    # Wake.feed must NOT be called during run_one_turn — start == end per turn.
    for start_calls, end_calls in turn_feed_call_pairs:
        assert start_calls == end_calls, (
            f"wake.feed ran during run_one_turn (start={start_calls}, end={end_calls}) "
            "— Pattern C invariant violated"
        )
    # State.LISTENING transition fires once per wake hit
    assert sm_transitions.count(State.LISTENING) >= 2


class _RaisingMicSource:
    """Mic source that raises a RuntimeError on second frames() entry.

    Used to verify that a wake-pump exception is NOT silently swallowed.
    First entry yields a few frames so the loop's first wake-fire path
    can complete; second entry (after run_one_turn) raises.
    """

    def __init__(self) -> None:
        self.entry_count = 0

    async def frames(self) -> AsyncIterator[AudioFrame]:
        self.entry_count += 1
        if self.entry_count >= 2:
            raise RuntimeError("simulated mic device error")
        for _ in range(5):
            yield (24_000, np.zeros(960, dtype=np.int16))
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_wake_pump_exception_propagates_not_silently_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guards against #79 review (Augment medium / Codex P1).

    If ``_run_wake_pump`` raises a non-CancelledError exception (mic
    device error, model failure), the exception must propagate out of
    ``_run_async`` instead of being silently dropped while the loop
    tight-restarts a broken pump.
    """
    mic = _RaisingMicSource()
    wake = MockWakeDetector(trigger_on_feed=True)

    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_mic_source",
        lambda **_: mic,
    )
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_speaker_sink",
        lambda **_: object(),
    )
    monkeypatch.setattr("reachy_ducky_app.main.load_default_wake_detector", lambda: wake)

    class _FakeVoice:
        def __init__(self, **_: Any) -> None: ...

    monkeypatch.setattr("reachy_ducky_app.main.OpenAIRealtimeVoice", _FakeVoice)

    fake_daemon = type(
        "FakeDaemon",
        (),
        {
            "from_env": classmethod(lambda cls: cls()),
            "aclose": staticmethod(lambda: asyncio.sleep(0)),
        },
    )()
    monkeypatch.setattr("reachy_ducky_app.main.DaemonClient", type(fake_daemon))

    async def _fake_run_one_turn(**_: Any) -> None:
        return None

    monkeypatch.setattr("reachy_ducky_app.main.run_one_turn", _fake_run_one_turn)
    monkeypatch.setattr(EmbodimentStateMachine, "transition", lambda self, target: None)

    stop_event = threading.Event()
    app = ReachyDuckyApp()

    with pytest.raises(RuntimeError, match="simulated mic device error"):
        await app._run_async(reachy_mini=object(), stop_event=stop_event)


@pytest.mark.asyncio
async def test_wake_fire_while_muted_does_not_run_turn_or_clear_mute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guards against #79 review (Codex P1).

    If wake.event fires while the state machine is MUTED (oWW false
    positive on near-silent input is the realistic trigger), the loop
    must NOT call sm.transition(State.LISTENING) — which would clear
    the mute gate — and must NOT run a turn. Mute contract: once muted,
    no turns until the user explicitly unmutes via the menubar/state
    machine.
    """
    mic = _CountingMicSource()
    wake = _FireOnceWake()
    sm_transitions: list[State] = []
    turn_calls: list[int] = []

    monkeypatch.setattr("reachy_ducky_app.main.load_default_mic_source", lambda **_: mic)
    monkeypatch.setattr("reachy_ducky_app.main.load_default_speaker_sink", lambda **_: object())
    monkeypatch.setattr("reachy_ducky_app.main.load_default_wake_detector", lambda: wake)

    class _FakeVoice:
        def __init__(self, **_: Any) -> None: ...

    monkeypatch.setattr("reachy_ducky_app.main.OpenAIRealtimeVoice", _FakeVoice)

    fake_daemon = type(
        "FakeDaemon",
        (),
        {
            "from_env": classmethod(lambda cls: cls()),
            "aclose": staticmethod(lambda: asyncio.sleep(0)),
        },
    )()
    monkeypatch.setattr("reachy_ducky_app.main.DaemonClient", type(fake_daemon))

    async def _fake_run_one_turn(**_: Any) -> None:
        turn_calls.append(1)

    monkeypatch.setattr("reachy_ducky_app.main.run_one_turn", _fake_run_one_turn)

    # Force the state machine to report MUTED and capture transition attempts.
    def _capture_transition(self: Any, target: State) -> None:
        sm_transitions.append(target)

    monkeypatch.setattr(EmbodimentStateMachine, "transition", _capture_transition)
    # The state property is what the loop reads — pin it to MUTED.
    monkeypatch.setattr(EmbodimentStateMachine, "state", property(lambda self: State.MUTED))

    stop_event = threading.Event()
    app = ReachyDuckyApp()

    async def _stop_after_a_few_wake_fires() -> None:
        # Give the loop time to fire wake at least twice (with backoff).
        deadline = asyncio.get_event_loop().time() + 1.0
        while wake.feed_calls < 5 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.01)
        stop_event.set()

    asyncio.create_task(_stop_after_a_few_wake_fires())
    await app._run_async(reachy_mini=object(), stop_event=stop_event)

    # The contract: while muted, wake-fire must not run any turn.
    assert turn_calls == [], "run_one_turn called despite MUTED — mute contract violated"
    assert State.LISTENING not in sm_transitions, (
        "transition(LISTENING) called while MUTED — would have cleared the mute gate"
    )
    # Wake DID fire and reset (proving we exercised the muted-fire path).
    assert wake.reset_calls >= 1
    assert wake.feed_calls >= 1
