"""Scripted test double for :class:`VoiceInterface`."""

from __future__ import annotations

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

    async def start_turn(self) -> VoiceTurn:
        return MockVoiceTurn(self._user_text)
