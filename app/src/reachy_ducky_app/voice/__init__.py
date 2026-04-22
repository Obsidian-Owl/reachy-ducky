"""Voice layer: abstract contract + scripted test double."""

from __future__ import annotations

from .interface import VoiceInterface, VoiceTurn
from .mock import MockVoice, MockVoiceTurn

__all__ = ["MockVoice", "MockVoiceTurn", "VoiceInterface", "VoiceTurn"]
