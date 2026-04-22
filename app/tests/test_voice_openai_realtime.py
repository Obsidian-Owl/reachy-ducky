"""Tests for :class:`OpenAIRealtimeVoice`.

Construction-shape tests and fake-connection drain tests live here. The
drain tests feed scripted ``AsyncRealtimeConnection`` event streams into
:meth:`OpenAIRealtimeVoiceTurn.speak_text` and
:meth:`OpenAIRealtimeVoiceTurn.get_user_text` to prove the event drain
and mic-pump loops work without needing a real WebSocket. The
``@pytest.mark.integration`` smoke at the bottom covers the live-session
path.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from collections.abc import AsyncIterator
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai.types.realtime import (
    RealtimeErrorEvent,
    ResponseAudioDeltaEvent,
    ResponseDoneEvent,
)
from openai.types.realtime.realtime_error import RealtimeError
from openai.types.realtime.realtime_response import RealtimeResponse
from reachy_ducky_app.voice.audio_io import (
    MicSource,
    MockMicSource,
    MockSpeakerSink,
)
from reachy_ducky_app.voice.openai_realtime import (
    OpenAIRealtimeVoice,
    OpenAIRealtimeVoiceTurn,
)


class _FakeConnection:
    """Scripted stand-in for ``openai`` ``AsyncRealtimeConnection``.

    Drives ``speak_text`` and ``get_user_text`` through a deterministic
    event sequence. The iterator records which events were consumed
    (``observed``) so tests can assert the drain went all the way to
    the terminal event and no further. ``response.create`` /
    ``response.cancel`` and ``input_audio_buffer.append`` are
    ``AsyncMock`` s so tests can assert the outbound side-effects.
    """

    def __init__(self, events: list[Any]) -> None:
        self._events = list(events)
        self.observed: list[Any] = []
        self.response = MagicMock()
        self.response.create = AsyncMock()
        self.response.cancel = AsyncMock()
        self.input_audio_buffer = MagicMock()
        self.input_audio_buffer.append = AsyncMock()

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._drain()

    async def _drain(self) -> AsyncIterator[Any]:
        # Yield control before producing each event so concurrent tasks
        # (e.g. the mic pump inside ``get_user_text``) get a scheduling
        # slice. Real ``AsyncRealtimeConnection`` iteration awaits a
        # websocket recv per event, which naturally yields; a synchronous
        # generator would starve co-tasks and mis-model the SDK.
        for event in self._events:
            await asyncio.sleep(0)
            self.observed.append(event)
            yield event


class _InfiniteMicSource(MicSource):
    """Mic stub that yields silence forever until cancelled.

    Used to verify the pump-task cancellation path in
    :meth:`OpenAIRealtimeVoiceTurn.get_user_text`: if the drain loop
    fails to cancel the pump, this source keeps yielding and the test
    times out.
    """

    def __init__(self) -> None:
        self.yielded: int = 0

    async def frames(self) -> AsyncIterator[bytes]:
        while True:
            self.yielded += 1
            yield b"silence"
            await asyncio.sleep(0)


class _RaisingMicSource(MicSource):
    """Mic stub that yields one frame then raises — exercises error propagation."""

    async def frames(self) -> AsyncIterator[bytes]:
        yield b"first"
        raise RuntimeError("mic lost")


class _ImmediateRaisingMicSource(MicSource):
    """Mic stub that raises synchronously on first access, before any ``yield``.

    Unlike :class:`_RaisingMicSource`, this variant's generator body reaches
    ``raise`` without any intervening ``await``, so the pump task completes
    deterministically within its first scheduling slice. Used to exercise
    the exact race Augment flagged: pump is ``done()`` with an exception
    stored by the time ``get_user_text`` returns an early transcript.
    """

    async def frames(self) -> AsyncIterator[bytes]:
        raise RuntimeError("mic dead")
        yield b""  # pragma: no cover — unreachable; keeps the function an async generator


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


def _make_audio_delta(pcm: bytes, *, event_id: str = "ev_audio") -> ResponseAudioDeltaEvent:
    """Build a minimal ``ResponseAudioDeltaEvent`` whose base64 delta decodes to ``pcm``."""
    return ResponseAudioDeltaEvent(
        content_index=0,
        delta=base64.b64encode(pcm).decode("ascii"),
        event_id=event_id,
        item_id="item_1",
        output_index=0,
        response_id="resp_1",
        type="response.output_audio.delta",
    )


def _make_transcription_completed(
    transcript: str,
) -> Any:
    """Build a minimal ``ConversationItemInputAudioTranscriptionCompletedEvent``.

    Imported inside the helper so the top-level import list stays short;
    the class name is long enough already. ``usage`` is required by the
    SDK model — we supply a zero-duration stub since tests don't assert
    on billing data.
    """
    from openai.types.realtime.conversation_item_input_audio_transcription_completed_event import (
        ConversationItemInputAudioTranscriptionCompletedEvent,
        UsageTranscriptTextUsageDuration,
    )

    return ConversationItemInputAudioTranscriptionCompletedEvent(
        content_index=0,
        event_id="ev_t",
        item_id="item_1",
        transcript=transcript,
        type="conversation.item.input_audio_transcription.completed",
        usage=UsageTranscriptTextUsageDuration(seconds=0.0, type="duration"),
    )


def test_construction_reads_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env-var path: OPENAI_API_KEY set, no kwarg → constructs cleanly."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    voice = OpenAIRealtimeVoice(mic=MockMicSource(), speaker=MockSpeakerSink())
    assert voice._api_key == "sk-from-env"


def test_construction_accepts_explicit_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit kwarg wins over env var (and over its absence)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    voice = OpenAIRealtimeVoice(
        mic=MockMicSource(),
        speaker=MockSpeakerSink(),
        api_key="sk-explicit",
    )
    assert voice._api_key == "sk-explicit"


def test_construction_prefers_explicit_api_key_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both sources are present, the explicit kwarg wins."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    voice = OpenAIRealtimeVoice(
        mic=MockMicSource(),
        speaker=MockSpeakerSink(),
        api_key="sk-explicit",
    )
    assert voice._api_key == "sk-explicit"


def test_construction_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var and no kwarg → ValueError at construction (fail fast)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIRealtimeVoice(mic=MockMicSource(), speaker=MockSpeakerSink())


def test_construction_passes_model_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """model= is stored verbatim on _model for start_turn to forward."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-any")
    voice = OpenAIRealtimeVoice(
        mic=MockMicSource(),
        speaker=MockSpeakerSink(),
        model="gpt-realtime-preview-2025",
    )
    assert voice._model == "gpt-realtime-preview-2025"


def test_construction_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default model is 'gpt-realtime' when not overridden."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-any")
    voice = OpenAIRealtimeVoice(mic=MockMicSource(), speaker=MockSpeakerSink())
    assert voice._model == "gpt-realtime"


def test_construction_stores_mic_and_speaker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mic + speaker are stored on the voice instance for start_turn to forward."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-any")
    mic = MockMicSource()
    speaker = MockSpeakerSink()
    voice = OpenAIRealtimeVoice(mic=mic, speaker=speaker)
    assert voice._mic is mic
    assert voice._speaker is speaker


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

    mic = MockMicSource()
    speaker = MockSpeakerSink()
    voice = OpenAIRealtimeVoice(mic=mic, speaker=speaker, model="gpt-realtime-test")

    async with voice.start_turn() as turn:
        # Manager entered; connection yielded as the turn's underlying connection.
        connect_manager.__aenter__.assert_awaited_once()
        connect_manager.__aexit__.assert_not_awaited()
        assert isinstance(turn, OpenAIRealtimeVoiceTurn)
        assert turn.connection is fake_connection
        # Turn forwards mic + speaker from the voice factory.
        assert turn._mic is mic
        assert turn._speaker is speaker

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

    voice = OpenAIRealtimeVoice(mic=MockMicSource(), speaker=MockSpeakerSink())

    class _BoomError(RuntimeError):
        pass

    with pytest.raises(_BoomError):
        async with voice.start_turn():
            raise _BoomError("explode mid-turn")

    connect_manager.__aenter__.assert_awaited_once()
    connect_manager.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_user_text_pumps_mic_frames_as_base64_append_events() -> None:
    """Mic frames are base64-encoded and sent via ``input_audio_buffer.append``.

    The original P1 bug: ``get_user_text`` iterated server events without
    ever pushing audio in, so the transcription event could never arrive.
    Now the method spawns a concurrent pump task that drains the mic
    source and calls ``append(audio=<base64>)`` for each frame.
    """
    mic = MockMicSource(frames=[b"f1", b"f2"])
    speaker = MockSpeakerSink()
    connection = _FakeConnection(events=[_make_transcription_completed("hi")])

    turn = OpenAIRealtimeVoiceTurn(connection, mic=mic, speaker=speaker)  # type: ignore[arg-type]

    result = await asyncio.wait_for(turn.get_user_text(), timeout=1.0)

    assert result == "hi"
    # Both scripted frames were appended, in order, base64-encoded.
    assert connection.input_audio_buffer.append.await_count == 2
    connection.input_audio_buffer.append.assert_any_await(
        audio=base64.b64encode(b"f1").decode("ascii")
    )
    connection.input_audio_buffer.append.assert_any_await(
        audio=base64.b64encode(b"f2").decode("ascii")
    )


@pytest.mark.asyncio
async def test_get_user_text_cancels_mic_pump_on_transcription() -> None:
    """When transcription arrives, the mic pump task must be cancelled.

    Uses an infinite mic source: if the pump is left running after
    ``get_user_text`` returns, the test coroutine never sees cancellation
    and the outer ``asyncio.wait_for`` backstop fires. The real
    correctness signal is that the test completes under the timeout.
    """
    mic = _InfiniteMicSource()
    speaker = MockSpeakerSink()
    connection = _FakeConnection(events=[_make_transcription_completed("done")])

    turn = OpenAIRealtimeVoiceTurn(connection, mic=mic, speaker=speaker)  # type: ignore[arg-type]

    result = await asyncio.wait_for(turn.get_user_text(), timeout=1.0)

    assert result == "done"
    # Side-effect verification: at least one frame was pumped before cancellation.
    assert connection.input_audio_buffer.append.await_count >= 1


@pytest.mark.asyncio
async def test_get_user_text_propagates_mic_errors() -> None:
    """A failing mic source surfaces its exception from ``get_user_text``.

    We do not silently swallow mic-layer errors — they indicate a real
    hardware-tier problem and the caller (the conversation loop) needs
    to see them so the state machine can recover or abort.
    """
    mic = _RaisingMicSource()
    speaker = MockSpeakerSink()
    # Connection yields no events: the drain loop will wait forever unless
    # the mic-pump exception propagates. We rely on the pump's error
    # reaching the outer coroutine.
    connection = _FakeConnection(events=[])

    turn = OpenAIRealtimeVoiceTurn(connection, mic=mic, speaker=speaker)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="mic lost"):
        await asyncio.wait_for(turn.get_user_text(), timeout=1.0)

    # The one pre-raise frame was pumped, proving the pump ran before failing.
    connection.input_audio_buffer.append.assert_awaited_once_with(
        audio=base64.b64encode(b"first").decode("ascii")
    )


@pytest.mark.asyncio
async def test_get_user_text_surfaces_mic_error_over_early_transcript_return() -> None:
    """Finally retrieves a completed pump task's exception even on early return.

    Augment PR #13 finding (medium): if the mic pump fails and a transcription
    event arrives, the early ``return event.transcript`` path fires before the
    inner pump-done check on line 185. The ``finally`` block must
    unconditionally retrieve the pump task's stored exception — otherwise the
    mic failure is silently swallowed (Python emits "Task exception was never
    retrieved") and the caller gets a stale transcript from a broken mic.

    A hardware-tier mic failure should supersede a stale transcript, so
    ``get_user_text`` must exit with the ``RuntimeError``, not return ``"hi"``.
    """
    mic = _ImmediateRaisingMicSource()
    speaker = MockSpeakerSink()
    # Single transcription event. _ImmediateRaisingMicSource raises without
    # any intervening await, so by the time the drain's pre-yield
    # ``await asyncio.sleep(0)`` completes, pump_task is deterministically
    # ``done()`` with RuntimeError stored.
    connection = _FakeConnection(events=[_make_transcription_completed("hi")])

    turn = OpenAIRealtimeVoiceTurn(connection, mic=mic, speaker=speaker)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="mic dead"):
        await asyncio.wait_for(turn.get_user_text(), timeout=1.0)

    # Pump raised before any append could fire.
    assert connection.input_audio_buffer.append.await_count == 0


@pytest.mark.asyncio
async def test_get_user_text_returns_empty_on_empty_event_stream() -> None:
    """Connection closes before transcription arrives: return ``""`` cleanly.

    Covers the "mic went silent, server closed" edge. The drain loop
    runs to exhaustion and the method returns an empty string rather
    than raising, so the caller can decide how to treat silence.
    """
    mic = MockMicSource()
    speaker = MockSpeakerSink()
    connection = _FakeConnection(events=[])

    turn = OpenAIRealtimeVoiceTurn(connection, mic=mic, speaker=speaker)  # type: ignore[arg-type]

    result = await asyncio.wait_for(turn.get_user_text(), timeout=1.0)

    assert result == ""
    # Nothing to pump from a default MockMicSource; 0 appends is correct.
    assert connection.input_audio_buffer.append.await_count == 0


@pytest.mark.asyncio
async def test_speak_text_pipes_audio_deltas_to_speaker() -> None:
    """Happy path: ``response.output_audio.delta`` payloads reach ``speaker.play``.

    The original P1 bug: ``speak_text`` drained to ``response.done`` but
    ignored ``response.output_audio.delta`` events, so the robot's
    speaker never heard the TTS audio. Now each delta is base64-decoded
    and forwarded to ``speaker.play(pcm)``.
    """
    delta1 = _make_audio_delta(b"pcm1", event_id="a1")
    delta2 = _make_audio_delta(b"pcm2", event_id="a2")
    done = _make_response_done("completed")
    connection = _FakeConnection(events=[delta1, delta2, done])

    speaker = MockSpeakerSink()
    turn = OpenAIRealtimeVoiceTurn(
        connection,  # type: ignore[arg-type]
        mic=MockMicSource(),
        speaker=speaker,
    )

    await turn.speak_text("hi")

    connection.response.create.assert_awaited_once()
    assert speaker.played == [b"pcm1", b"pcm2"]
    # Drain consumed all three events and stopped at the terminal one.
    assert connection.observed == [delta1, delta2, done]


@pytest.mark.asyncio
async def test_speak_text_ignores_non_delta_events_while_piping_audio() -> None:
    """Deltas interleaved with non-audio events: only deltas reach the speaker."""
    delta1 = _make_audio_delta(b"A")
    interstitial = MagicMock(name="unrelated_event")
    delta2 = _make_audio_delta(b"B")
    done = _make_response_done("completed")
    connection = _FakeConnection(events=[delta1, interstitial, delta2, done])

    speaker = MockSpeakerSink()
    turn = OpenAIRealtimeVoiceTurn(
        connection,  # type: ignore[arg-type]
        mic=MockMicSource(),
        speaker=speaker,
    )

    await turn.speak_text("hi")

    connection.response.create.assert_awaited_once()
    assert speaker.played == [b"A", b"B"]
    assert connection.observed == [delta1, interstitial, delta2, done]


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

    turn = OpenAIRealtimeVoiceTurn(
        connection,  # type: ignore[arg-type]
        mic=MockMicSource(),
        speaker=MockSpeakerSink(),
    )

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

    turn = OpenAIRealtimeVoiceTurn(
        connection,  # type: ignore[arg-type]
        mic=MockMicSource(),
        speaker=MockSpeakerSink(),
    )

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

    turn = OpenAIRealtimeVoiceTurn(
        connection,  # type: ignore[arg-type]
        mic=MockMicSource(),
        speaker=MockSpeakerSink(),
    )

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

    turn = OpenAIRealtimeVoiceTurn(
        connection,  # type: ignore[arg-type]
        mic=MockMicSource(),
        speaker=MockSpeakerSink(),
    )

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
    voice = OpenAIRealtimeVoice(mic=MockMicSource(), speaker=MockSpeakerSink())
    async with voice.start_turn() as turn:
        assert isinstance(turn, OpenAIRealtimeVoiceTurn)
