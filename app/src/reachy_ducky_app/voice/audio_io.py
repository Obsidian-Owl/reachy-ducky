"""Pluggable mic-in / speaker-out contracts for the voice layer.

Mirrors the :class:`~reachy_ducky_app.wake.WakeDetector` +
:class:`~reachy_ducky_app.embodiment.motion_driver.MotionDriver` pattern:
define a narrow ABC, ship a ``MockImpl`` unit-testable without hardware,
ship a ``load_default_*`` factory that returns the mock today and the
hardware-backed impl when a ``ReachyMini`` is wired through.

Audio shape: :data:`AudioFrame` = ``(sample_rate, int16 mono ndarray)``.
The reference conversation app
(``pollen-robotics/reachy_mini_conversation_app``) carries audio between
its mic-pump, LLM session, and head-wobble driver as raw
``(sample_rate, ndarray)`` tuples. We mirror that here so parallel
consumers (head wobble, transcript-synced lighting, VU meter) can read
the same frame at different rates without resampling twice — see
``console.py:625-628`` in the reference for the canonical example.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import cast

import numpy as np
import numpy.typing as npt
import scipy.signal

# ``AudioFrame = (sample_rate, int16 mono ndarray)``. Matches the raw-tuple
# shape used by pollen-robotics/reachy_mini_conversation_app's
# AsyncStreamHandler pattern (console.py:569-630) so our adapters are
# composable with parallel consumers like an audio-driven head-wobble
# driver without resampling twice. The mono constraint is deliberate —
# OpenAI Realtime expects mono PCM16 and the reference pipeline collapses
# stereo → left channel at the adapter boundary. PEP 695 ``type`` syntax
# (Python 3.12+) is canonical here per ruff UP040.
type AudioFrame = tuple[int, npt.NDArray[np.int16]]

# Module-level constants for clarity at the call sites.
_SDK_AUDIO_RATE = 16000  # ``AudioBase.SAMPLE_RATE`` from the reachy_mini SDK.
_LLM_AUDIO_RATE = 24000  # OpenAI Realtime default PCM16 mono.


class MicSource(ABC):
    """Async source of :data:`AudioFrame` tuples from a mic.

    Each frame is ``(sample_rate, int16 mono samples)``. The sample rate
    is carried explicitly (not implicit to the ABC) so upstream consumers
    can resample or route to rate-specific sinks without peeking into a
    subclass. The reference conversation app threads the same int16
    ndarray into BOTH a resampling LLM handler AND a head-wobble driver
    at different rates — tuples make that trivial.
    """

    @abstractmethod
    def frames(self) -> AsyncIterator[AudioFrame]:
        """Yield audio frames until the turn ends or the source closes."""


class SpeakerSink(ABC):
    """Async sink that plays :data:`AudioFrame` tuples through a speaker."""

    @abstractmethod
    async def play(self, frame: AudioFrame) -> None:
        """Play one ``(sample_rate, int16 mono samples)`` frame."""


class MockMicSource(MicSource):
    """Replays a scripted sequence of :data:`AudioFrame` tuples then terminates.

    Default behaviour yields nothing — a bare ``MockMicSource()`` simulates
    a silent mic (stream ends immediately). Pass
    ``frames=[(sample_rate, ndarray), ...]`` to replay a scripted payload
    sequence — useful for driving the full turn orchestration in tests.
    """

    def __init__(self, frames: Sequence[AudioFrame] | None = None) -> None:
        self._frames = tuple(frames) if frames is not None else ()

    async def frames(self) -> AsyncIterator[AudioFrame]:
        for frame in self._frames:
            yield frame


class MockSpeakerSink(SpeakerSink):
    """Captures played :data:`AudioFrame` tuples in order for test assertions."""

    def __init__(self) -> None:
        self.played: list[AudioFrame] = []

    async def play(self, frame: AudioFrame) -> None:
        self.played.append(frame)


class ReachyMicSource(MicSource):
    """Hardware-backed mic source: pulls frames from the ReachyMini SDK.

    Wraps ``ReachyMini.media.get_audio_sample()`` in an async generator.
    The SDK call is synchronous and runs on an executor so the voice
    event loop isn't blocked.

    **Format conversion at the adapter boundary.** The SDK returns
    ``float32 stereo (N, 2)`` at 16 kHz (``AudioBase.SAMPLE_RATE`` /
    ``CHANNELS``); OpenAI Realtime expects ``int16 mono`` at 24 kHz. This
    adapter collapses stereo → mono by picking the left channel
    (matching ``openai_realtime.py:760`` in the reference), resamples
    16 kHz → 24 kHz via :func:`scipy.signal.resample`, and casts to
    int16 by ``np.clip(x, -1, 1) * 32767`` (matches
    ``fastrtc.audio_to_int16`` which the reference uses).

    **Hardware-only.** Constructor takes a duck-typed ``reachy_mini``
    whose ``.media`` exposes ``get_audio_sample()``; the factory
    :func:`load_default_mic_source` selects this impl over
    :class:`MockMicSource` when a non-None ``reachy_mini`` is passed.
    """

    def __init__(self, reachy_mini: object) -> None:
        self._mini = reachy_mini

    async def frames(self) -> AsyncIterator[AudioFrame]:
        """Yield ``(24000, int16 mono)`` tuples until the SDK returns ``None``."""
        loop = asyncio.get_running_loop()
        get_sample = self._mini.media.get_audio_sample  # type: ignore[attr-defined]
        while True:
            sample = await loop.run_in_executor(None, get_sample)
            if sample is None:
                return
            # Collapse stereo (N, 2) → mono. Pick channel 0 per the
            # conversation-app reference (openai_realtime.py:760).
            mono = sample[:, 0] if sample.ndim == 2 else sample
            # Resample 16 kHz → 24 kHz. scipy.signal.resample is FFT-
            # based; matches the reference pipeline. ``scipy.signal``
            # ships no py.typed marker so pyright infers a union return
            # type; the implementation always returns an ndarray when
            # passed a 1-D array (the with-time variant returns a tuple,
            # which we don't request) — cast to keep downstream typing
            # strict.
            resampled = cast(
                npt.NDArray[np.float32],
                scipy.signal.resample(mono, int(len(mono) * _LLM_AUDIO_RATE / _SDK_AUDIO_RATE)),
            )
            # Float32 [-1, 1] → int16 via * 32767 (matches
            # fastrtc.audio_to_int16 which the reference uses).
            int16 = (np.clip(resampled, -1.0, 1.0) * 32767).astype(np.int16)
            yield (_LLM_AUDIO_RATE, int16)


class ReachySpeakerSink(SpeakerSink):
    """Hardware-backed speaker sink: pushes frames to the ReachyMini SDK.

    Symmetric with :class:`ReachyMicSource`: unpacks an
    :data:`AudioFrame` tuple, converts int16 → float32 via
    ``/ 32768`` (matches ``fastrtc.audio_to_float32`` and the
    conversation-app reference — asymmetric with the mic's ``* 32767``
    is a deliberate PCM16 round-trip pattern), resamples to the SDK's
    16 kHz output rate via :func:`scipy.signal.resample`, and pushes
    **mono** float32 to ``push_audio_sample``. The SDK auto-fans
    mono → stereo internally (``media_manager.py:357-358``), so we do
    NOT duplicate channels here.

    **Hardware-only.** Same selection semantics as
    :class:`ReachyMicSource` via :func:`load_default_speaker_sink`.
    """

    def __init__(self, reachy_mini: object) -> None:
        self._mini = reachy_mini

    async def play(self, frame: AudioFrame) -> None:
        """Play one ``(sample_rate, int16 mono)`` frame through the SDK."""
        sample_rate, samples = frame
        loop = asyncio.get_running_loop()
        push = self._mini.media.push_audio_sample  # type: ignore[attr-defined]
        # int16 → float32 via / 32768 (fastrtc convention — asymmetric
        # with mic's * 32767 is a deliberate PCM16 round-trip pattern).
        float32: npt.NDArray[np.float32] = samples.astype(np.float32) / 32768.0
        # Resample to SDK's 16 kHz if the incoming rate differs.
        if sample_rate != _SDK_AUDIO_RATE:
            # See note in ReachyMicSource on the scipy.signal.resample
            # narrow-cast — same return-type-union issue here.
            resampled = cast(
                npt.NDArray[np.float32],
                scipy.signal.resample(float32, int(len(float32) * _SDK_AUDIO_RATE / sample_rate)),
            )
            float32 = resampled.astype(np.float32)
        await loop.run_in_executor(None, push, float32)


def load_default_mic_source(*, reachy_mini: object | None = None) -> MicSource:
    """Return :class:`ReachyMicSource` when ``reachy_mini`` is given, else mock.

    The on-robot Pollen daemon hands :meth:`ReachyDuckyApp.run` a live
    ``ReachyMini`` instance; :meth:`ReachyDuckyApp._run_async` threads it
    through this factory so production is hardware by default. Dev
    machines and unit tests pass ``None`` (the default) and get the
    silent :class:`MockMicSource`. ``reachy_mini`` is keyword-only so
    future kwargs (e.g. M2's ``mute_gate``) can be added without
    positional churn.
    """
    if reachy_mini is None:
        return MockMicSource()
    return ReachyMicSource(reachy_mini)


def load_default_speaker_sink(*, reachy_mini: object | None = None) -> SpeakerSink:
    """Return :class:`ReachySpeakerSink` when ``reachy_mini`` is given, else mock.

    Symmetric with :func:`load_default_mic_source` — production path
    selects the hardware-backed adapter when the on-robot daemon hands
    us a live ``ReachyMini``; dev/unit path keeps the silent mock.
    Keyword-only ``reachy_mini`` for forward-compat (M2 mute gate).
    """
    if reachy_mini is None:
        return MockSpeakerSink()
    return ReachySpeakerSink(reachy_mini)
