"""Abstract voice layer for the Reachy-side app."""

from __future__ import annotations

from abc import ABC, abstractmethod


class VoiceTurn(ABC):
    """A single user-Ducky conversational turn.

    Concrete implementations wrap a realtime session (OpenAI Realtime via
    fastrtc, etc.) OR a deterministic test double. The public surface is
    intentionally narrow: the brain-layer only needs to get user text,
    speak a reply, and optionally cut the reply short.
    """

    @abstractmethod
    async def get_user_text(self) -> str:
        """Block until the realtime layer emits a final user transcript."""

    @abstractmethod
    async def speak_text(self, text: str) -> None:
        """Send ``text`` to the realtime layer for TTS + playback."""

    @abstractmethod
    async def interrupt(self) -> None:
        """Cancel any in-flight reply (barge-in)."""


class VoiceInterface(ABC):
    """Factory for :class:`VoiceTurn`.

    Pluggable: ``OpenAIRealtimeVoice`` (Task 8.2) and future Gemini Live /
    bring-your-own implementations all satisfy this contract.
    """

    @abstractmethod
    async def start_turn(self) -> VoiceTurn:
        """Begin a new conversational turn."""
