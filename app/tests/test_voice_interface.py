"""Tests for the abstract voice layer and its scripted mock."""

from __future__ import annotations

import pytest
from reachy_ducky_app.voice import MockVoice, MockVoiceTurn, VoiceInterface, VoiceTurn


def test_voice_interface_is_abstract() -> None:
    """VoiceInterface cannot be instantiated directly — it is an ABC."""
    with pytest.raises(TypeError):
        VoiceInterface()  # type: ignore[abstract]


def test_voice_turn_is_abstract() -> None:
    """VoiceTurn cannot be instantiated directly — it is an ABC."""
    with pytest.raises(TypeError):
        VoiceTurn()  # type: ignore[abstract]


async def test_mock_voice_starts_turn_returning_voice_turn_instance() -> None:
    """MockVoice.start_turn returns a concrete VoiceTurn (MockVoiceTurn)."""
    voice = MockVoice(scripted_user_text="hello")
    turn = await voice.start_turn()
    assert isinstance(turn, VoiceTurn)
    assert isinstance(turn, MockVoiceTurn)


async def test_mock_voice_turn_delivers_scripted_user_text() -> None:
    """get_user_text returns the text the MockVoice was scripted with."""
    voice = MockVoice(scripted_user_text="hi ducky")
    turn = await voice.start_turn()
    assert await turn.get_user_text() == "hi ducky"


async def test_mock_voice_turn_records_spoken_text() -> None:
    """speak_text appends to spoken_texts for side-effect assertions."""
    turn = MockVoiceTurn(user_text="ignored")
    await turn.speak_text("reply")
    assert turn.spoken_texts == ["reply"]


async def test_mock_voice_turn_records_multiple_speaks() -> None:
    """Multiple speak_text calls accumulate in call order."""
    turn = MockVoiceTurn(user_text="ignored")
    await turn.speak_text("first")
    await turn.speak_text("second")
    await turn.speak_text("third")
    assert turn.spoken_texts == ["first", "second", "third"]


async def test_mock_voice_turn_interrupt_sets_flag() -> None:
    """interrupt() sets the .interrupted flag so barge-in is observable."""
    turn = MockVoiceTurn(user_text="ignored")
    await turn.interrupt()
    assert turn.interrupted is True


def test_mock_voice_turn_interrupt_default_false() -> None:
    """A fresh MockVoiceTurn starts with interrupted == False."""
    turn = MockVoiceTurn(user_text="ignored")
    assert turn.interrupted is False
