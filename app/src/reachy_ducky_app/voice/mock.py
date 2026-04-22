"""Scripted test double for :class:`VoiceInterface`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from .interface import VoiceInterface, VoiceTurn


class MockVoiceTurn(VoiceTurn):
    """In-memory :class:`VoiceTurn` that records side effects for assertions."""

    def __init__(self, user_text: str) -> None:
        self._user_text = user_text
        self.spoken_texts: list[str] = []
        self.interrupted: bool = False

    async def get_user_text(self) -> str:
        return self._user_text

    async def speak_text(self, text: str) -> None:
        self.spoken_texts.append(text)

    async def interrupt(self) -> None:
        self.interrupted = True


class MockVoice(VoiceInterface):
    """Test double. Scripted user text; captures everything spoken."""

    def __init__(
        self,
        *,
        scripted_user_text: str,
        scripted_reply_text: str = "",
    ) -> None:
        self._user_text = scripted_user_text
        # Reserved for future use: tests that want to assert the reply path
        # without driving it themselves.
        self._reply = scripted_reply_text

    def start_turn(self) -> AbstractAsyncContextManager[MockVoiceTurn]:
        """Return an async context manager that yields a :class:`MockVoiceTurn`.

        The mock has no underlying connection to release, but the shape
        matches :class:`~reachy_ducky_app.voice.openai_realtime.OpenAIRealtimeVoice`'s
        so tests exercise the same control-flow as production. Subclasses
        (e.g. the conversation-test spies) override this method and
        wrap/replace the returned CM; declaring the return type as
        :class:`AbstractAsyncContextManager` lets those overrides compose
        naturally without fighting mypy about the private
        ``_AsyncGeneratorContextManager`` coming out of
        ``@asynccontextmanager``.
        """
        return self._make_turn_cm()

    @asynccontextmanager
    async def _make_turn_cm(self) -> AsyncIterator[MockVoiceTurn]:
        yield MockVoiceTurn(self._user_text)
