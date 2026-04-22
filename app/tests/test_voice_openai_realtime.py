"""Tests for :class:`OpenAIRealtimeVoice`.

Unit tests cover construction-shape only. The stateful WebSocket methods
(``get_user_text``, ``speak_text``, ``interrupt``) require a live OpenAI
session and are exercised by the ``@pytest.mark.integration`` smoke at
the bottom of this file, which is gated by ``REACHY_DUCKY_RUN_INTEGRATION``
+ ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from reachy_ducky_app.voice.openai_realtime import (
    OpenAIRealtimeVoice,
    OpenAIRealtimeVoiceTurn,
)


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


@pytest.mark.asyncio
async def test_start_turn_enters_and_exits_sdk_connect_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``start_turn`` must both enter *and* exit the SDK's connect() CM.

    Regression for bug I1 (websocket leak): the old code called
    ``manager.enter()`` and never closed the connection. With the
    async-context-manager shape, the outer ``async with`` in the caller
    drives ``__aexit__`` on the SDK manager, releasing the websocket.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    # Fake AsyncRealtimeConnection: minimal surface start_turn touches is
    # `.session.update(...)`. The turn object stores it untouched.
    fake_connection = MagicMock(name="AsyncRealtimeConnection")
    fake_connection.session = MagicMock()
    fake_connection.session.update = AsyncMock()

    # Fake AsyncRealtimeConnectionManager: an async context manager.
    connect_manager = MagicMock(name="AsyncRealtimeConnectionManager")
    connect_manager.__aenter__ = AsyncMock(return_value=fake_connection)
    connect_manager.__aexit__ = AsyncMock(return_value=None)

    # Fake AsyncOpenAI: its .realtime.connect(model=...) returns the CM.
    fake_realtime = MagicMock()
    fake_realtime.connect = MagicMock(return_value=connect_manager)
    fake_client = MagicMock()
    fake_client.realtime = fake_realtime

    def _fake_async_openai(*, api_key: str) -> MagicMock:
        assert api_key == "sk-test"
        return fake_client

    monkeypatch.setattr("openai.AsyncOpenAI", _fake_async_openai)

    voice = OpenAIRealtimeVoice(model="gpt-realtime-test")

    async with voice.start_turn() as turn:
        # Manager entered; connection yielded as the turn's underlying connection.
        connect_manager.__aenter__.assert_awaited_once()
        connect_manager.__aexit__.assert_not_awaited()
        assert isinstance(turn, OpenAIRealtimeVoiceTurn)
        assert turn.connection is fake_connection

    # After the async-with in this test exits: manager must have been exited.
    connect_manager.__aexit__.assert_awaited_once()
    fake_realtime.connect.assert_called_once_with(model="gpt-realtime-test")


@pytest.mark.asyncio
async def test_start_turn_exits_sdk_connect_manager_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exception inside the ``async with`` body must still close the CM."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    fake_connection = MagicMock(name="AsyncRealtimeConnection")
    fake_connection.session = MagicMock()
    fake_connection.session.update = AsyncMock()

    connect_manager = MagicMock(name="AsyncRealtimeConnectionManager")
    connect_manager.__aenter__ = AsyncMock(return_value=fake_connection)
    connect_manager.__aexit__ = AsyncMock(return_value=None)

    fake_realtime = MagicMock()
    fake_realtime.connect = MagicMock(return_value=connect_manager)
    fake_client = MagicMock()
    fake_client.realtime = fake_realtime

    def _fake_async_openai(*, api_key: str) -> MagicMock:
        return fake_client

    monkeypatch.setattr("openai.AsyncOpenAI", _fake_async_openai)

    voice = OpenAIRealtimeVoice()

    class _BoomError(RuntimeError):
        pass

    with pytest.raises(_BoomError):
        async with voice.start_turn():
            raise _BoomError("explode mid-turn")

    connect_manager.__aenter__.assert_awaited_once()
    connect_manager.__aexit__.assert_awaited_once()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_realtime_smoke() -> None:
    """Live smoke: open a real realtime WebSocket session.

    Gated by ``REACHY_DUCKY_RUN_INTEGRATION=1`` AND ``OPENAI_API_KEY``.
    Same pattern as the Task 2.3 Claude OAuth smoke: verify construction
    + session-open succeed against the real API. We do not feed audio
    (that needs a mic) and we do not wait for transcripts — scope is
    strictly "does session-open work." The ``async with`` drives
    teardown of the SDK connection so the test process exits cleanly.
    """
    if not os.environ.get("REACHY_DUCKY_RUN_INTEGRATION"):
        pytest.skip("set REACHY_DUCKY_RUN_INTEGRATION=1 to run")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    voice = OpenAIRealtimeVoice()
    async with voice.start_turn() as turn:
        assert isinstance(turn, OpenAIRealtimeVoiceTurn)
