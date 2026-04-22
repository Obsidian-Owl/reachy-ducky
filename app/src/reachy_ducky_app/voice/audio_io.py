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

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence


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


def load_default_mic_source() -> MicSource:
    """Factory for the production mic source.

    Phase A returns a silent :class:`MockMicSource`. A follow-up issue
    swaps in a ``ReachyMicSource`` once the Reachy audio API is bound;
    callers should never instantiate the mock directly outside tests.
    """
    return MockMicSource()


def load_default_speaker_sink() -> SpeakerSink:
    """Factory for the production speaker sink.

    Phase A returns a dropping :class:`MockSpeakerSink`. A follow-up
    issue swaps in a ``ReachySpeakerSink`` once the Reachy audio API is
    bound; callers should never instantiate the mock directly outside
    tests.
    """
    return MockSpeakerSink()
