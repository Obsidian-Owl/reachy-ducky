"""Voice layer: abstract contract + scripted test double + OpenAI Realtime."""

from __future__ import annotations

from .audio_io import (
    MicSource,
    MockMicSource,
    MockSpeakerSink,
    SpeakerSink,
    load_default_mic_source,
    load_default_speaker_sink,
)
from .interface import VoiceInterface, VoiceTurn
from .mock import MockVoice, MockVoiceTurn
from .openai_realtime import OpenAIRealtimeVoice, OpenAIRealtimeVoiceTurn

__all__ = [
    "MicSource",
    "MockMicSource",
    "MockSpeakerSink",
    "MockVoice",
    "MockVoiceTurn",
    "OpenAIRealtimeVoice",
    "OpenAIRealtimeVoiceTurn",
    "SpeakerSink",
    "VoiceInterface",
    "VoiceTurn",
    "load_default_mic_source",
    "load_default_speaker_sink",
]
