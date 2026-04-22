"""Voice layer: abstract contract + scripted test double + OpenAI Realtime."""

from __future__ import annotations

from .interface import VoiceInterface, VoiceTurn
from .mock import MockVoice, MockVoiceTurn
from .openai_realtime import OpenAIRealtimeVoice, OpenAIRealtimeVoiceTurn

__all__ = [
    "MockVoice",
    "MockVoiceTurn",
    "OpenAIRealtimeVoice",
    "OpenAIRealtimeVoiceTurn",
    "VoiceInterface",
    "VoiceTurn",
]
