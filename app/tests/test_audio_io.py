"""Tests for :mod:`reachy_ducky_app.voice.audio_io` — the pluggable audio I/O layer.

Mirrors the :mod:`test_wake` shape: unit-test the mock impls and the
load-default factory. The real Reachy-backed mic + speaker land later
as a hardware-tier follow-up; when they do, those tests live in a
hardware-marked module and these stay unit-tier.
"""

from __future__ import annotations

import pytest
from reachy_ducky_app.voice.audio_io import (
    MicSource,
    MockMicSource,
    MockSpeakerSink,
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
