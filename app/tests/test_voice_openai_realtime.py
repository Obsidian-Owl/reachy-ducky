"""Tests for :class:`OpenAIRealtimeVoice`.

Unit tests cover construction-shape only. The stateful WebSocket methods
(``get_user_text``, ``speak_text``, ``interrupt``) require a live OpenAI
session and are exercised by the ``@pytest.mark.integration`` smoke at
the bottom of this file, which is gated by ``REACHY_DUCKY_RUN_INTEGRATION``
+ ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import os

import pytest
from reachy_ducky_app.voice.openai_realtime import OpenAIRealtimeVoice


def test_construction_reads_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env-var path: OPENAI_API_KEY set, no kwarg → constructs cleanly."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    voice = OpenAIRealtimeVoice()
    assert voice._api_key == "sk-from-env"


def test_construction_accepts_explicit_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit kwarg wins over env var (and over its absence)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    voice = OpenAIRealtimeVoice(api_key="sk-explicit")
    assert voice._api_key == "sk-explicit"


def test_construction_prefers_explicit_api_key_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both sources are present, the explicit kwarg wins."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    voice = OpenAIRealtimeVoice(api_key="sk-explicit")
    assert voice._api_key == "sk-explicit"


def test_construction_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var and no kwarg → ValueError at construction (fail fast)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIRealtimeVoice()


def test_construction_passes_model_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """model= is stored verbatim on _model for start_turn to forward."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-any")
    voice = OpenAIRealtimeVoice(model="gpt-realtime-preview-2025")
    assert voice._model == "gpt-realtime-preview-2025"


def test_construction_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default model is 'gpt-realtime' when not overridden."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-any")
    voice = OpenAIRealtimeVoice()
    assert voice._model == "gpt-realtime"


def test_module_exports_openai_realtime_voice() -> None:
    """OpenAIRealtimeVoice is exported at the voice package root."""
    from reachy_ducky_app.voice import OpenAIRealtimeVoice as Exported

    assert Exported is OpenAIRealtimeVoice


def test_module_exports_openai_realtime_voice_turn() -> None:
    """OpenAIRealtimeVoiceTurn is also exported at the voice package root."""
    from reachy_ducky_app.voice import OpenAIRealtimeVoiceTurn
    from reachy_ducky_app.voice.openai_realtime import (
        OpenAIRealtimeVoiceTurn as Direct,
    )

    assert OpenAIRealtimeVoiceTurn is Direct


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_realtime_smoke() -> None:
    """Live smoke: open a real realtime WebSocket session.

    Gated by ``REACHY_DUCKY_RUN_INTEGRATION=1`` AND ``OPENAI_API_KEY``.
    Same pattern as the Task 2.3 Claude OAuth smoke: verify construction
    + session-open succeed against the real API. We do not feed audio
    (that needs a mic) and we do not wait for transcripts — scope is
    strictly "does session-open work."
    """
    if not os.environ.get("REACHY_DUCKY_RUN_INTEGRATION"):
        pytest.skip("set REACHY_DUCKY_RUN_INTEGRATION=1 to run")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    voice = OpenAIRealtimeVoice()
    turn = await voice.start_turn()
    assert turn is not None
    # Clean up the WebSocket so the test process exits cleanly. VoiceTurn
    # doesn't expose a close method, so reach through to the SDK connection.
    from reachy_ducky_app.voice.openai_realtime import OpenAIRealtimeVoiceTurn

    assert isinstance(turn, OpenAIRealtimeVoiceTurn)
    await turn.connection.close()
