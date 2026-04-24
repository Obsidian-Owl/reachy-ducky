"""Tests for :mod:`reachy_ducky_app.voice.audio_io` — the pluggable audio I/O layer.

Mirrors the :mod:`test_wake` shape: unit-test the mock impls, the
hardware-backed ``Reachy*`` adapters via ``FakeMedia`` /  ``FakeMini``
fixtures, and the load-default factory selection. The ``FakeMedia`` mic
fixture mimics the real SDK's ``(N, 2)`` stereo float32 output shape so
the adapter's stereo→mono collapse path is exercised end-to-end without
hardware.
"""

from __future__ import annotations

import asyncio
import contextlib

import numpy as np
import pytest
from reachy_ducky_app.voice.audio_io import (
    AudioFrame,
    MicSource,
    MockMicSource,
    MockSpeakerSink,
    ReachyMicSource,
    ReachySpeakerSink,
    SpeakerSink,
    load_default_mic_source,
    load_default_speaker_sink,
)

# Constants mirroring the adapter's internal SDK / LLM rates so test
# fixtures and assertions don't accidentally drift from the source of
# truth in audio_io.py.
_SDK_RATE = 16000
_LLM_RATE = 24000


class _FakeMedia:
    """Stand-in for ``reachy_mini.media.MediaManager`` covering the audio surface.

    Mic side: pops scripted stereo (N, 2) float32 samples until exhausted,
    then raises :class:`asyncio.CancelledError` to simulate task-level
    cancellation (the only correct way to terminate the adapter's
    now-infinite polling loop — ``None`` is transient per
    :mod:`test_sdk_audio_contract`). Speaker side: appends every
    ``push_audio_sample`` payload into ``pushed`` for assertions. Also
    exposes the samplerate getters so the FakeMedia stays
    forward-compatible if the adapter ever consults them (today it
    hardcodes 16 kHz to match the real SDK's ``AudioBase.SAMPLE_RATE``).
    """

    def __init__(self, scripted: list[np.ndarray] | None = None) -> None:
        self._scripted = list(scripted) if scripted is not None else []
        self.pushed: list[np.ndarray] = []

    def get_audio_sample(self) -> np.ndarray | None:
        if not self._scripted:
            raise asyncio.CancelledError
        return self._scripted.pop(0)

    def push_audio_sample(self, data: np.ndarray) -> None:
        self.pushed.append(data)

    def get_input_audio_samplerate(self) -> int:
        return _SDK_RATE

    def get_output_audio_samplerate(self) -> int:
        return _SDK_RATE


class _FakeMini:
    """Stand-in for ``ReachyMini`` exposing only ``.media``."""

    def __init__(self, media: _FakeMedia) -> None:
        self.media = media


# ---------------------------------------------------------------------------
# Mock impl tests
# ---------------------------------------------------------------------------


async def test_mock_mic_source_default_yields_no_frames() -> None:
    """``MockMicSource()`` with no scripted frames terminates immediately.

    Simulates a silent mic — the stream ends rather than hanging, so
    callers that block on ``async for frame in mic.frames()`` fall
    through cleanly when no audio arrives.
    """
    mic = MockMicSource()
    collected: list[AudioFrame] = []
    async for frame in mic.frames():
        collected.append(frame)
    assert collected == []


async def test_mock_mic_source_replays_scripted_frames_in_order() -> None:
    """Scripted frames are yielded verbatim in the order passed to ``__init__``."""
    f1: AudioFrame = (_LLM_RATE, np.array([1, 2, 3], dtype=np.int16))
    f2: AudioFrame = (_LLM_RATE, np.array([4, 5, 6], dtype=np.int16))
    mic = MockMicSource(frames=[f1, f2])

    collected: list[AudioFrame] = []
    async for frame in mic.frames():
        collected.append(frame)

    assert len(collected) == 2
    assert collected[0][0] == _LLM_RATE
    np.testing.assert_array_equal(collected[0][1], f1[1])
    assert collected[1][0] == _LLM_RATE
    np.testing.assert_array_equal(collected[1][1], f2[1])


async def test_mock_speaker_sink_accumulates_plays() -> None:
    """``MockSpeakerSink.play`` records each :data:`AudioFrame` in call order."""
    sink = MockSpeakerSink()
    frame_a: AudioFrame = (_LLM_RATE, np.array([1, 2], dtype=np.int16))
    frame_b: AudioFrame = (_LLM_RATE, np.array([3, 4], dtype=np.int16))
    await sink.play(frame_a)
    await sink.play(frame_b)
    assert len(sink.played) == 2
    assert sink.played[0][0] == _LLM_RATE
    np.testing.assert_array_equal(sink.played[0][1], frame_a[1])
    np.testing.assert_array_equal(sink.played[1][1], frame_b[1])


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


def test_load_default_mic_source_returns_mic_source() -> None:
    """Factory returns a :class:`MicSource` (today the mock with no kwargs)."""
    mic = load_default_mic_source()
    assert isinstance(mic, MicSource)


def test_load_default_speaker_sink_returns_speaker_sink() -> None:
    """Factory returns a :class:`SpeakerSink` (today the mock with no kwargs)."""
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


# ---------------------------------------------------------------------------
# ReachyMicSource — hardware adapter mic path
# ---------------------------------------------------------------------------


async def test_reachy_mic_source_collapses_resamples_and_casts_to_int16() -> None:
    """The full mic conversion chain: stereo float32 → mono → 24 kHz int16 tuple.

    Pins the canonical reference flow (``console.py:569-578``,
    ``openai_realtime.py:760``):

    1. SDK returns ``(N, 2)`` float32 at 16 kHz.
    2. Adapter picks left channel → mono (N,).
    3. Resamples 16 → 24 kHz via FFT-based ``scipy.signal.resample``
       (target length ``N * 24000 / 16000 == 1.5 * N``).
    4. Clips to ``[-1, 1]`` and casts ``* 32767`` → int16.
    5. Yields ``(24000, int16_samples)`` tuple.

    A constant 0.5 input lets us assert on a tight expected magnitude
    band that survives FFT-resample numerical jitter at the edges.
    """
    # 480 samples @ 16 kHz = 30 ms — a typical mic chunk.
    stereo_frame = np.full((480, 2), 0.5, dtype=np.float32)
    media = _FakeMedia(scripted=[stereo_frame])
    src = ReachyMicSource(_FakeMini(media))

    collected: list[AudioFrame] = []
    with contextlib.suppress(asyncio.CancelledError):
        async for frame in src.frames():
            collected.append(frame)

    # Exactly one frame yielded then end-of-stream.
    assert len(collected) == 1
    sample_rate, int16 = collected[0]
    assert sample_rate == _LLM_RATE
    assert int16.dtype == np.int16
    # Resample 16→24 kHz scales length by 1.5 — exact for this clean ratio.
    assert int16.shape == (720,)
    # Constant 0.5 in → ~16383 int16. FFT resample introduces small ringing
    # at the boundaries, so check the steady-state interior with a tight
    # tolerance and the full array with a slightly looser tolerance.
    np.testing.assert_allclose(int16[100:-100], 16383, atol=20)


async def test_reachy_mic_source_keeps_polling_on_transient_none() -> None:
    """SDK returns None transiently while GStreamer warms up.

    The adapter must keep polling — mic is always recording, only task
    cancellation terminates. Pinning this so a future refactor that
    accidentally converts None → end-of-stream gets caught before
    reaching hardware (where startup would see 0 frames).
    """
    sequence: list[np.ndarray | None] = [
        None,
        None,
        np.full((480, 2), 0.1, dtype=np.float32),
        None,
        np.full((480, 2), 0.2, dtype=np.float32),
    ]

    class _FakeMediaTransient:
        _i = 0

        def get_audio_sample(self) -> np.ndarray | None:
            if _FakeMediaTransient._i >= len(sequence):
                raise asyncio.CancelledError  # simulate task cancel
            frame = sequence[_FakeMediaTransient._i]
            _FakeMediaTransient._i += 1
            return frame

    class _FakeMiniTransient:
        media = _FakeMediaTransient()

    src = ReachyMicSource(_FakeMiniTransient())

    collected: list[AudioFrame] = []
    with contextlib.suppress(asyncio.CancelledError):
        async for frame in src.frames():
            collected.append(frame)

    # Two non-None frames yielded despite Nones interleaved.
    assert len(collected) == 2, f"expected 2 frames, got {len(collected)}"
    # Both at the LLM rate.
    for sr, _ in collected:
        assert sr == _LLM_RATE


async def test_reachy_mic_source_handles_mono_input_without_collapsing() -> None:
    """Defensive: if the SDK ever returns mono ``(N,)``, adapter passes it through.

    The real SDK only returns stereo ``(N, 2)`` per ``audio_base.py:61``,
    but covering the mono branch keeps the conditional honest under
    coverage and protects against an upstream simplification.
    """
    mono_frame = np.full(480, 0.25, dtype=np.float32)
    media = _FakeMedia(scripted=[mono_frame])
    src = ReachyMicSource(_FakeMini(media))

    collected: list[AudioFrame] = []
    with contextlib.suppress(asyncio.CancelledError):
        async for frame in src.frames():
            collected.append(frame)

    assert len(collected) == 1
    sample_rate, int16 = collected[0]
    assert sample_rate == _LLM_RATE
    assert int16.dtype == np.int16
    assert int16.shape == (720,)


async def test_reachy_mic_source_is_a_mic_source() -> None:
    """``ReachyMicSource`` satisfies the :class:`MicSource` structural contract."""
    src = ReachyMicSource(_FakeMini(_FakeMedia(scripted=[])))
    assert isinstance(src, MicSource)


# ---------------------------------------------------------------------------
# ReachySpeakerSink — hardware adapter speaker path
# ---------------------------------------------------------------------------


async def test_reachy_speaker_sink_resamples_and_pushes_mono_float32() -> None:
    """Full speaker conversion chain: int16 24 kHz tuple → float32 16 kHz mono.

    Pins the canonical reference flow:

    1. ABC accepts ``(24000, int16 mono samples)`` tuple.
    2. Adapter casts ``int16 / 32768`` → float32 in [-1, 1].
    3. Resamples 24 → 16 kHz (target length ``N * 2/3``).
    4. Pushes **mono** float32 to the SDK; the SDK auto-fans to stereo
       internally per ``media_manager.py:357-358`` — we do NOT
       duplicate channels at this layer.
    """
    media = _FakeMedia()
    sink = ReachySpeakerSink(_FakeMini(media))

    # 720 samples @ 24 kHz = 30 ms. Constant 16384 ≈ 0.5 float32.
    int16_samples = np.full(720, 16384, dtype=np.int16)
    frame: AudioFrame = (_LLM_RATE, int16_samples)
    await sink.play(frame)

    assert len(media.pushed) == 1
    pushed = media.pushed[0]
    assert pushed.dtype == np.float32
    # 24 → 16 kHz drops length by 2/3 (exact for this clean ratio).
    assert pushed.shape == (480,)
    # Constant 16384 / 32768 = 0.5 expected; FFT ringing at edges, tight in middle.
    np.testing.assert_allclose(pushed[60:-60], 16384 / 32768.0, atol=1e-3)


async def test_reachy_speaker_sink_skips_resample_when_rate_matches_sdk() -> None:
    """If the incoming rate already equals the SDK rate, no resample happens.

    Length is preserved verbatim and values are the int16 / 32768 cast.
    Pins the fast-path branch in ``ReachySpeakerSink.play``.
    """
    media = _FakeMedia()
    sink = ReachySpeakerSink(_FakeMini(media))

    int16_samples = np.full(480, 8192, dtype=np.int16)
    frame: AudioFrame = (_SDK_RATE, int16_samples)
    await sink.play(frame)

    assert len(media.pushed) == 1
    pushed = media.pushed[0]
    assert pushed.dtype == np.float32
    assert pushed.shape == (480,)
    # No resample → exact 8192 / 32768 = 0.25 everywhere.
    np.testing.assert_allclose(pushed, 0.25, atol=1e-7)


async def test_reachy_speaker_sink_forwards_multiple_frames_in_order() -> None:
    """Multiple ``play()`` calls forward to the SDK in call order."""
    media = _FakeMedia()
    sink = ReachySpeakerSink(_FakeMini(media))

    frame_a: AudioFrame = (_SDK_RATE, np.full(480, 1000, dtype=np.int16))
    frame_b: AudioFrame = (_SDK_RATE, np.full(480, -1000, dtype=np.int16))
    frame_c: AudioFrame = (_SDK_RATE, np.zeros(480, dtype=np.int16))

    await sink.play(frame_a)
    await sink.play(frame_b)
    await sink.play(frame_c)

    assert len(media.pushed) == 3
    np.testing.assert_allclose(media.pushed[0], 1000 / 32768.0, rtol=1e-6)
    np.testing.assert_allclose(media.pushed[1], -1000 / 32768.0, rtol=1e-6)
    np.testing.assert_allclose(media.pushed[2], 0.0, atol=1e-9)


async def test_reachy_speaker_sink_is_a_speaker_sink() -> None:
    """Structural: ReachySpeakerSink satisfies the SpeakerSink contract."""
    sink = ReachySpeakerSink(_FakeMini(_FakeMedia()))
    assert isinstance(sink, SpeakerSink)


# ---------------------------------------------------------------------------
# Factory selection
# ---------------------------------------------------------------------------


def test_load_default_mic_source_returns_mock_when_reachy_mini_is_none() -> None:
    """No ``reachy_mini=`` -> :class:`MockMicSource` (dev-machine / unit-test path)."""
    src = load_default_mic_source(reachy_mini=None)
    assert isinstance(src, MockMicSource)


def test_load_default_mic_source_returns_hardware_impl_when_reachy_mini_given() -> None:
    """A ReachyMini-like object -> :class:`ReachyMicSource`.

    Factory only checks truthiness; the returned adapter stores the
    object for later ``frames()`` calls (which would then touch
    ``.media.get_audio_sample``). A bare stand-in suffices here.
    """

    class _BareFakeMini:
        pass

    src = load_default_mic_source(reachy_mini=_BareFakeMini())
    assert isinstance(src, ReachyMicSource)


def test_load_default_speaker_sink_returns_mock_when_reachy_mini_is_none() -> None:
    sink = load_default_speaker_sink(reachy_mini=None)
    assert isinstance(sink, MockSpeakerSink)


def test_load_default_speaker_sink_returns_hardware_impl_when_reachy_mini_given() -> None:
    class _BareFakeMini:
        pass

    sink = load_default_speaker_sink(reachy_mini=_BareFakeMini())
    assert isinstance(sink, ReachySpeakerSink)


def test_load_default_factories_default_reachy_mini_to_none() -> None:
    """Back-compat: factories callable with no args (returns mocks)."""
    assert isinstance(load_default_mic_source(), MockMicSource)
    assert isinstance(load_default_speaker_sink(), MockSpeakerSink)


def test_load_default_factories_require_keyword_for_reachy_mini() -> None:
    """``reachy_mini`` is keyword-only — positional args raise ``TypeError``.

    Pins I-4 from the M1 review: future kwargs (e.g. M2's ``mute_gate``)
    can be added without positional churn. A positional call here would
    bypass that protection silently.
    """

    class _BareFakeMini:
        pass

    with pytest.raises(TypeError):
        load_default_mic_source(_BareFakeMini())  # type: ignore[misc]
    with pytest.raises(TypeError):
        load_default_speaker_sink(_BareFakeMini())  # type: ignore[misc]
