"""Abstract voice layer for the Reachy-side app."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager


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

    ``start_turn`` returns an **async context manager** (not a coroutine
    yielding a ``VoiceTurn``). Callers use it as::

        async with voice.start_turn() as turn:
            user = await turn.get_user_text()
            await turn.speak_text(reply)
        # resources released here

    This matches the OpenAI SDK's own ``AsyncRealtimeConnectionManager``
    shape and enforces websocket cleanup at the type level — the outer
    ``async with`` drives the implementation's ``__aexit__`` whether the
    body succeeded or raised, so per-turn websockets cannot leak.
    """

    @abstractmethod
    def start_turn(self) -> AbstractAsyncContextManager[VoiceTurn]:
        """Begin a new conversational turn.

        Not ``async def`` on purpose: this returns a context-manager
        object (typically from ``@asynccontextmanager``) which the caller
        then drives with ``async with``.
        """
