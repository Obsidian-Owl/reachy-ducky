"""Tests for :class:`OpenAIRealtimeVoice`.

Construction-shape tests and fake-connection drain tests live here. The
drain tests feed scripted ``AsyncRealtimeConnection`` event streams into
:meth:`OpenAIRealtimeVoiceTurn.speak_text` to prove it consumes events
until the terminal ``response.done`` arrives without needing a real
WebSocket. The ``@pytest.mark.integration`` smoke at the bottom covers
the live-session path.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai.types.realtime import RealtimeErrorEvent, ResponseDoneEvent
from openai.types.realtime.realtime_error import RealtimeError
from openai.types.realtime.realtime_response import RealtimeResponse
from reachy_ducky_app.voice.openai_realtime import (
    OpenAIRealtimeVoice,
    OpenAIRealtimeVoiceTurn,
)


class _FakeConnection:
    """Scripted stand-in for ``openai`` ``AsyncRealtimeConnection``.

    Drives ``speak_text`` through a deterministic event sequence. The
    iterator records which events were consumed (``observed``) so tests
    can assert the drain went all the way to the terminal event and no
    further. ``response.create`` / ``response.cancel`` are ``AsyncMock``s
    so tests can assert the outbound side-effects.
    """

    def __init__(self, events: list[Any]) -> None:
        self._events = list(events)
        self.observed: list[Any] = []
        self.response = MagicMock()
        self.response.create = AsyncMock()
        self.response.cancel = AsyncMock()

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._drain()

    async def _drain(self) -> AsyncIterator[Any]:
        for event in self._events:
            self.observed.append(event)
            yield event


def _make_response_done(
    status: Literal["completed", "cancelled", "failed", "incomplete", "in_progress"] = (
        "completed"
    ),
) -> ResponseDoneEvent:
    """Build a minimal ``ResponseDoneEvent`` with the given response status."""
    return ResponseDoneEvent(
        event_id="ev_done",
        response=RealtimeResponse(id="resp_1", status=status),
        type="response.done",
    )


def _make_error_event(message: str = "boom") -> RealtimeErrorEvent:
    """Build a minimal session-level ``RealtimeErrorEvent``."""
    return RealtimeErrorEvent(
        event_id="ev_err",
        error=RealtimeError(type="server_error", message=message),
        type="error",
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

    # Fake AsyncRealtimeConnection: start_turn does not touch any of its
    # attrs — it just yields it as the turn's .connection. A plain
    # MagicMock is enough.
    fake_connection = MagicMock(name="AsyncRealtimeConnection")

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


@pytest.mark.asyncio
async def test_speak_text_drains_events_until_response_done() -> None:
    """Happy path: ``speak_text`` returns only after ``ResponseDoneEvent``.

    Regression for the post-I1 bug where ``speak_text`` returned as soon
    as ``response.create`` was awaited, so the outer async-with closed
    the websocket before the server finished streaming audio. The drain
    loop must consume all intermediate events and stop on ``response.done``.
    """
    interstitial = MagicMock(name="response.audio.delta")
    done = _make_response_done("completed")
    connection = _FakeConnection(events=[interstitial, done])

    turn = OpenAIRealtimeVoiceTurn(connection)  # type: ignore[arg-type]

    await turn.speak_text("hi")

    connection.response.create.assert_awaited_once()
    payload = connection.response.create.await_args.kwargs["response"]
    assert payload == {
        "output_modalities": ["audio", "text"],
        "instructions": "hi",
    }
    # Drain consumed both events and stopped at the terminal one.
    assert connection.observed == [interstitial, done]


@pytest.mark.asyncio
async def test_speak_text_returns_on_failed_response_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Server-side response failure: ``speak_text`` logs and returns.

    Best-effort TTS semantics — the state machine progresses even if the
    Realtime API reports a failed response. We still want a breadcrumb
    in the log so a debug session can see why audio went quiet.
    """
    failed = _make_response_done("failed")
    connection = _FakeConnection(events=[failed])

    turn = OpenAIRealtimeVoiceTurn(connection)  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING, logger="reachy_ducky_app.voice.openai_realtime"):
        await turn.speak_text("hi")

    connection.response.create.assert_awaited_once()
    assert connection.observed == [failed]
    assert any(
        "realtime response" in record.message.lower() and record.levelno == logging.WARNING
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_speak_text_returns_on_session_error_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Session-level ``error`` event: log and return (no hang, no raise)."""
    err = _make_error_event("connection lost")
    connection = _FakeConnection(events=[err])

    turn = OpenAIRealtimeVoiceTurn(connection)  # type: ignore[arg-type]

    with caplog.at_level(logging.WARNING, logger="reachy_ducky_app.voice.openai_realtime"):
        await turn.speak_text("hi")

    connection.response.create.assert_awaited_once()
    assert connection.observed == [err]


@pytest.mark.asyncio
async def test_speak_text_returns_on_cancelled_response() -> None:
    """Concurrent ``interrupt()`` triggers a cancel; the server emits
    ``response.done`` with ``status == "cancelled"``; ``speak_text`` returns.

    This is how barge-in works end-to-end: the drain loop observes the
    terminal event the server sent in response to our cancel, rather
    than consulting ``self._interrupted`` directly.
    """
    cancelled = _make_response_done("cancelled")
    connection = _FakeConnection(events=[cancelled])

    turn = OpenAIRealtimeVoiceTurn(connection)  # type: ignore[arg-type]

    async def _drive_speak() -> None:
        await turn.speak_text("hi")

    async def _drive_interrupt() -> None:
        # Yield once so speak_text gets a chance to await response.create
        # before we fire the cancel — mirrors realistic ordering.
        await asyncio.sleep(0)
        await turn.interrupt()

    await asyncio.gather(_drive_speak(), _drive_interrupt())

    connection.response.create.assert_awaited_once()
    connection.response.cancel.assert_awaited_once()
    assert turn._interrupted is True
    assert connection.observed == [cancelled]


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
