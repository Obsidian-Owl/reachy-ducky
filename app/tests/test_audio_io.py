"""Tests for :mod:`reachy_ducky_app.voice.audio_io` — the pluggable audio I/O layer.

Mirrors the :mod:`test_wake` shape: unit-test the mock impls and the
load-default factory. The real Reachy-backed mic + speaker land later
as a hardware-tier follow-up; when they do, those tests live in a
hardware-marked module and these stay unit-tier.
"""

from __future__ import annotations

import numpy as np
import pytest
from reachy_ducky_app.voice.audio_io import (
    MicSource,
    MockMicSource,
    MockSpeakerSink,
    ReachyMicSource,
    ReachySpeakerSink,
    SpeakerSink,
    load_default_mic_source,
    load_default_speaker_sink,
)


async def test_mock_mic_source_default_yields_no_frames() -> None:
    """``MockMicSource()`` with no scripted frames terminates immediately.

    Simulates a silent mic — the stream ends rather than hanging, so
    callers that block on ``async for frame in mic.frames()`` fall
    through cleanly when no audio arrives.
    """
    mic = MockMicSource()
    collected: list[bytes] = []
    async for frame in mic.frames():
        collected.append(frame)
    assert collected == []


async def test_mock_mic_source_replays_scripted_frames_in_order() -> None:
    """Scripted frames are yielded in the order passed to ``__init__``."""
    mic = MockMicSource(frames=[b"f1", b"f2", b"f3"])
    collected: list[bytes] = []
    async for frame in mic.frames():
        collected.append(frame)
    assert collected == [b"f1", b"f2", b"f3"]


async def test_mock_speaker_sink_accumulates_plays() -> None:
    """``MockSpeakerSink.play`` records each PCM frame in call order."""
    sink = MockSpeakerSink()
    await sink.play(b"x")
    await sink.play(b"y")
    assert sink.played == [b"x", b"y"]


def test_load_default_mic_source_returns_mic_source() -> None:
    """Phase A: the factory returns a :class:`MicSource` (today a mock).

    Isinstance check guards the contract: callers depending on the ABC
    don't break when the factory is swapped to a hardware impl later.
    """
    mic = load_default_mic_source()
    assert isinstance(mic, MicSource)


def test_load_default_speaker_sink_returns_speaker_sink() -> None:
    """Phase A: the factory returns a :class:`SpeakerSink` (today a mock)."""
    sink = load_default_speaker_sink()
    assert isinstance(sink, SpeakerSink)


def test_mic_source_is_abstract() -> None:
    """``MicSource`` cannot be instantiated directly — it's an ABC."""
    with pytest.raises(TypeError):
        MicSource()  # type: ignore[abstract]


def test_speaker_sink_is_abstract() -> None:
    """``SpeakerSink`` cannot be instantiated directly — it's an ABC."""
    with pytest.raises(TypeError):
        SpeakerSink()  # type: ignore[abstract]


async def test_reachy_mic_source_yields_pcm16_frames_from_float32_source() -> None:
    """``ReachyMicSource`` pulls float32 frames from the SDK and yields PCM16 bytes.

    Pins the adapter-boundary conversion: the SDK returns
    ``npt.NDArray[np.float32]`` in ``[-1, 1]``; the :class:`MicSource`
    ABC yields PCM16 mono 24 kHz bytes (matches the OpenAI Realtime
    API downstream). A scripted ``FakeMedia`` keeps this hardware-free.
    """
    scripted = [
        np.full(480, 0.1, dtype=np.float32),
        np.full(480, 0.2, dtype=np.float32),
        np.full(480, 0.3, dtype=np.float32),
    ]
    expected_bytes = [(np.clip(f, -1.0, 1.0) * 32767).astype(np.int16).tobytes() for f in scripted]

    class FakeMedia:
        _i = 0
        _scripted = scripted

        def get_audio_sample(self) -> np.ndarray | None:
            if FakeMedia._i >= len(FakeMedia._scripted):
                return None
            frame = FakeMedia._scripted[FakeMedia._i]
            FakeMedia._i += 1
            return frame

    class FakeMini:
        media = FakeMedia()

    src = ReachyMicSource(FakeMini())
    collected = [frame async for frame in src.frames()]

    assert collected == expected_bytes


async def test_reachy_mic_source_terminates_on_none_sentinel() -> None:
    """Adapter stops when the SDK returns ``None`` (no more data available)."""
    call_count = 0

    class FakeMedia:
        def get_audio_sample(self) -> np.ndarray | None:
            nonlocal call_count
            call_count += 1
            if call_count > 2:
                return None
            return np.full(480, 0.5, dtype=np.float32)

    class FakeMini:
        media = FakeMedia()

    src = ReachyMicSource(FakeMini())
    frames = [frame async for frame in src.frames()]

    assert len(frames) == 2
    assert call_count == 3  # two data frames + one None terminator


async def test_reachy_mic_source_is_a_mic_source() -> None:
    """``ReachyMicSource`` satisfies the :class:`MicSource` structural contract."""

    class FakeMedia:
        def get_audio_sample(self) -> np.ndarray | None:
            return None

    class FakeMini:
        media = FakeMedia()

    src = ReachyMicSource(FakeMini())
    assert isinstance(src, MicSource)


async def test_reachy_speaker_sink_converts_pcm16_to_float32() -> None:
    """SpeakerSink ABC takes PCM16 bytes; SDK takes float32 ndarray in [-1, 1].

    Adapter converts at the boundary: ``np.frombuffer(pcm, int16).astype(float32) / 32767``.
    """
    received: list[np.ndarray] = []

    class FakeMedia:
        def push_audio_sample(self, data: np.ndarray) -> None:
            received.append(data)

    class FakeMini:
        media = FakeMedia()

    # Construct a known PCM16 frame: int16 value 16384 = 0.5 float32 (approx).
    int16_frame = np.full(480, 16384, dtype=np.int16)
    pcm_bytes = int16_frame.tobytes()

    sink = ReachySpeakerSink(FakeMini())
    await sink.play(pcm_bytes)

    assert len(received) == 1
    forwarded = received[0]
    assert forwarded.dtype == np.float32
    assert forwarded.shape == (480,)
    # 16384 / 32767 ≈ 0.5000153
    np.testing.assert_allclose(forwarded, 16384 / 32767, rtol=1e-6)


async def test_reachy_speaker_sink_forwards_multiple_frames() -> None:
    """Multiple ``play()`` calls forward to the SDK in order."""
    received: list[np.ndarray] = []

    class FakeMedia:
        def push_audio_sample(self, data: np.ndarray) -> None:
            received.append(data)

    class FakeMini:
        media = FakeMedia()

    sink = ReachySpeakerSink(FakeMini())

    frame_a = np.full(480, 1000, dtype=np.int16).tobytes()
    frame_b = np.full(480, -1000, dtype=np.int16).tobytes()
    frame_c = np.zeros(480, dtype=np.int16).tobytes()

    await sink.play(frame_a)
    await sink.play(frame_b)
    await sink.play(frame_c)

    assert len(received) == 3
    # Verify per-frame conversion correctness: positive, negative, zero.
    np.testing.assert_allclose(received[0], 1000 / 32767, rtol=1e-6)
    np.testing.assert_allclose(received[1], -1000 / 32767, rtol=1e-6)
    np.testing.assert_allclose(received[2], 0.0, atol=1e-9)


async def test_reachy_speaker_sink_is_a_speaker_sink() -> None:
    """Structural: ReachySpeakerSink satisfies the SpeakerSink contract."""

    class FakeMedia:
        def push_audio_sample(self, data: np.ndarray) -> None:
            pass

    class FakeMini:
        media = FakeMedia()

    sink = ReachySpeakerSink(FakeMini())
    assert isinstance(sink, SpeakerSink)


def test_load_default_mic_source_returns_mock_when_reachy_mini_is_none() -> None:
    """No reachy_mini -> MockMicSource (dev-machine / unit-test path)."""
    src = load_default_mic_source(reachy_mini=None)
    assert isinstance(src, MockMicSource)


def test_load_default_mic_source_returns_hardware_impl_when_reachy_mini_given() -> None:
    """A ReachyMini-like object -> ReachyMicSource.

    Factory only checks truthiness; the returned adapter stores the
    object for later ``frames()`` calls (which would then touch
    ``.media.get_audio_sample``). A bare stand-in suffices here.
    """

    class FakeMini:
        pass

    src = load_default_mic_source(reachy_mini=FakeMini())
    assert isinstance(src, ReachyMicSource)


def test_load_default_speaker_sink_returns_mock_when_reachy_mini_is_none() -> None:
    sink = load_default_speaker_sink(reachy_mini=None)
    assert isinstance(sink, MockSpeakerSink)


def test_load_default_speaker_sink_returns_hardware_impl_when_reachy_mini_given() -> None:
    class FakeMini:
        pass

    sink = load_default_speaker_sink(reachy_mini=FakeMini())
    assert isinstance(sink, ReachySpeakerSink)


def test_load_default_factories_default_reachy_mini_to_none() -> None:
    """Back-compat: factories callable with no args (returns mocks)."""
    assert isinstance(load_default_mic_source(), MockMicSource)
    assert isinstance(load_default_speaker_sink(), MockSpeakerSink)
