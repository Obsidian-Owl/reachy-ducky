"""Pluggable mic-in / speaker-out contracts for the voice layer.

Mirrors the :class:`~reachy_ducky_app.wake.WakeDetector` +
:class:`~reachy_ducky_app.embodiment.motion_driver.MotionDriver` pattern:
define a narrow ABC, ship a ``MockImpl`` unit-testable without hardware,
ship a ``load_default_*`` factory that returns the mock today and will
return a hardware-backed impl once the Reachy audio API is bound
(tracked as a follow-up).

Audio format: PCM16 little-endian at 24 kHz mono — the format the
OpenAI Realtime API expects on both input and output. The SDK's
``input_audio_buffer.append`` and ``response.output_audio.delta`` events
carry audio as base64-encoded strings; callers of :class:`MicSource`
and :class:`SpeakerSink` work in raw PCM bytes, and the voice layer
handles base64 framing at the connection boundary.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence

import numpy as np


class MicSource(ABC):
    """Async source of PCM16 mono 24 kHz mic frames."""

    @abstractmethod
    def frames(self) -> AsyncIterator[bytes]:
        """Yield PCM audio frames until the turn ends or the source closes."""


class SpeakerSink(ABC):
    """Async sink that plays PCM16 mono 24 kHz frames through a speaker."""

    @abstractmethod
    async def play(self, pcm: bytes) -> None:
        """Play one PCM frame. Implementations may buffer internally."""


class MockMicSource(MicSource):
    """Replays a scripted sequence of PCM frames then terminates.

    Default behaviour yields nothing — a bare ``MockMicSource()`` simulates
    a silent mic (stream ends immediately). Pass ``frames=[b"...", ...]``
    to replay a scripted payload sequence — useful for driving the full
    turn orchestration in tests.
    """

    def __init__(self, frames: Sequence[bytes] | None = None) -> None:
        self._frames = tuple(frames) if frames is not None else ()

    async def frames(self) -> AsyncIterator[bytes]:
        for frame in self._frames:
            yield frame


class MockSpeakerSink(SpeakerSink):
    """Captures played PCM frames in order for test assertions."""

    def __init__(self) -> None:
        self.played: list[bytes] = []

    async def play(self, pcm: bytes) -> None:
        self.played.append(pcm)


class ReachyMicSource(MicSource):
    """Hardware-backed mic source: pulls PCM frames from the ReachyMini SDK.

    Wraps ``ReachyMini.media.get_audio_sample()`` in an async generator.
    The SDK call is synchronous and runs on an executor so the voice
    event loop isn't blocked.

    **Format conversion at the adapter boundary.** The SDK returns
    ``npt.NDArray[np.float32]`` (values in ``[-1.0, 1.0]`` per the
    ``MediaManager`` contract); the :class:`MicSource` ABC yields PCM16
    mono 24 kHz bytes (matches the OpenAI Realtime API downstream).
    This class converts float32 → PCM16-bytes per frame: clip to
    ``[-1, 1]``, scale by 32767, cast to int16, ``.tobytes()``. If a
    future SDK version returns a different dtype, the
    :mod:`test_sdk_audio_contract` drift guard fails first.

    **Hardware-only.** Constructors take a duck-typed ``reachy_mini``
    whose ``.media`` exposes ``get_audio_sample()``; the factory
    :func:`load_default_mic_source` selects this impl over
    :class:`MockMicSource` when a non-None ``reachy_mini`` is passed
    (Task 1.4).
    """

    def __init__(self, reachy_mini: object) -> None:
        self._mini = reachy_mini

    async def frames(self) -> AsyncIterator[bytes]:
        """Yield PCM16 bytes until the SDK returns None or an empty buffer."""
        loop = asyncio.get_running_loop()
        get_sample = self._mini.media.get_audio_sample  # type: ignore[attr-defined]
        while True:
            frame = await loop.run_in_executor(None, get_sample)
            if frame is None or frame.size == 0:
                return
            pcm16 = (np.clip(frame, -1.0, 1.0) * 32767).astype(np.int16)
            yield pcm16.tobytes()


class ReachySpeakerSink(SpeakerSink):
    """Hardware-backed speaker sink: pushes PCM frames to the ReachyMini SDK.

    Wraps ``ReachyMini.media.push_audio_sample()``. Symmetric with
    :class:`ReachyMicSource`: synchronous SDK call is dispatched to an
    executor so the voice loop stays unblocked.

    **Format conversion at the adapter boundary.** The SpeakerSink ABC
    takes PCM16 mono 24 kHz bytes (matches the OpenAI Realtime API
    upstream); the SDK takes ``npt.NDArray[np.float32]`` (values in
    ``[-1.0, 1.0]``). This class converts PCM16-bytes → float32 per
    frame: ``np.frombuffer(pcm, dtype=np.int16).astype(np.float32)``
    divided by 32767. If a future SDK version takes a different dtype,
    the :mod:`test_sdk_audio_contract` drift guard fails first.

    **Hardware-only.** Constructors take a duck-typed ``reachy_mini``
    whose ``.media`` exposes ``push_audio_sample()``; the factory
    :func:`load_default_speaker_sink` selects this impl over
    :class:`MockSpeakerSink` when a non-None ``reachy_mini`` is passed
    (Task 1.4).
    """

    def __init__(self, reachy_mini: object) -> None:
        self._mini = reachy_mini

    async def play(self, pcm: bytes) -> None:
        """Play one PCM16 frame through the SDK speaker (async via executor)."""
        loop = asyncio.get_running_loop()
        push = self._mini.media.push_audio_sample  # type: ignore[attr-defined]
        int16 = np.frombuffer(pcm, dtype=np.int16)
        float32 = int16.astype(np.float32) / 32767
        await loop.run_in_executor(None, push, float32)


def load_default_mic_source(reachy_mini: object | None = None) -> MicSource:
    """Return :class:`ReachyMicSource` when ``reachy_mini`` is given, else mock.

    The on-robot Pollen daemon hands :meth:`ReachyDuckyApp.run` a live
    ``ReachyMini`` instance; :meth:`ReachyDuckyApp._run_async` threads it
    through this factory so production is hardware by default. Dev
    machines and unit tests pass ``None`` (the default) and get the
    silent :class:`MockMicSource`.
    """
    if reachy_mini is None:
        return MockMicSource()
    return ReachyMicSource(reachy_mini)


def load_default_speaker_sink(reachy_mini: object | None = None) -> SpeakerSink:
    """Return :class:`ReachySpeakerSink` when ``reachy_mini`` is given, else mock.

    Symmetric with :func:`load_default_mic_source` — production path
    selects the hardware-backed adapter when the on-robot daemon hands
    us a live ``ReachyMini``; dev/unit path keeps the silent mock.
    """
    if reachy_mini is None:
        return MockSpeakerSink()
    return ReachySpeakerSink(reachy_mini)
