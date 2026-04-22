"""Tests for :func:`run_one_turn` — the single-turn conversation orchestrator.

Integration-style unit tests. Each test composes the real voice mock,
the real motion-driver mock, the real state machine, and a
pytest-httpx-mocked :class:`DaemonClient`. No network, no hardware.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest
from pytest_httpx import HTTPXMock
from reachy_ducky_app.conversation import run_one_turn
from reachy_ducky_app.daemon_client import DaemonClient
from reachy_ducky_app.embodiment.motion_driver import MockMotionDriver
from reachy_ducky_app.embodiment.state_machine import EmbodimentStateMachine
from reachy_ducky_app.voice.mock import MockVoice, MockVoiceTurn
from reachy_ducky_protocol.messages import State


class _SpyMockVoice(MockVoice):
    """``MockVoice`` that counts ``start_turn`` calls for no-op assertions.

    ``start_turn`` is an async context manager, so the spy wraps ``super().
    start_turn()`` in its own CM that bumps the counter on entry.
    """

    def __init__(self, *, scripted_user_text: str) -> None:
        super().__init__(scripted_user_text=scripted_user_text)
        self.start_turn_calls: int = 0

    def start_turn(self) -> AbstractAsyncContextManager[MockVoiceTurn]:
        self.start_turn_calls += 1
        return super().start_turn()


@pytest.mark.asyncio
async def test_run_one_turn_full_flow(httpx_mock: HTTPXMock) -> None:
    """Happy path: LISTENING → THINKING → LISTENING (speak) → IDLE."""
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        json={"text": "you're on main", "specialist_invoked": None},
    )
    voice = MockVoice(scripted_user_text="what's on my branch?")
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    daemon = DaemonClient(base_url="http://127.0.0.1:8765")

    await run_one_turn(voice=voice, sm=sm, daemon=daemon, project_slug="demo")

    # Motion sequence: IDLE→LISTENING (listening), LISTENING→THINKING
    # (thinking), THINKING→LISTENING (listening), LISTENING→IDLE (neutral).
    assert driver.moves == ["listening", "thinking", "listening", "neutral"]
    assert sm.state == State.IDLE


@pytest.mark.asyncio
async def test_run_one_turn_speaks_reply_text_verbatim(
    httpx_mock: HTTPXMock,
) -> None:
    """The daemon's reply text is passed through ``speak_text`` unchanged."""
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        json={"text": "hello world", "specialist_invoked": None},
    )
    voice = MockVoice(scripted_user_text="hi")
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    daemon = DaemonClient(base_url="http://127.0.0.1:8765")

    # Capture the turn by wrapping start_turn's returned context manager.
    captured: list[MockVoiceTurn] = []
    original_start = voice.start_turn

    @asynccontextmanager
    async def spy_start() -> AsyncIterator[MockVoiceTurn]:
        async with original_start() as turn:
            captured.append(turn)
            yield turn

    voice.start_turn = spy_start  # type: ignore[method-assign]

    await run_one_turn(voice=voice, sm=sm, daemon=daemon)

    assert len(captured) == 1
    turn = captured[0]
    assert isinstance(turn, MockVoiceTurn)
    assert turn.spoken_texts == ["hello world"]


@pytest.mark.asyncio
async def test_run_one_turn_passes_project_slug(httpx_mock: HTTPXMock) -> None:
    """``project_slug`` is forwarded into the daemon request body."""
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        json={"text": "ok", "specialist_invoked": None},
    )
    voice = MockVoice(scripted_user_text="hi")
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    daemon = DaemonClient(base_url="http://127.0.0.1:8765")

    await run_one_turn(voice=voice, sm=sm, daemon=daemon, project_slug="demo")

    sent = httpx_mock.get_request()
    assert sent is not None
    body = json.loads(sent.content)
    assert body["project_slug"] == "demo"
    assert body["user_utterance"] == "hi"


@pytest.mark.asyncio
async def test_run_one_turn_uses_primary_when_no_slug(
    httpx_mock: HTTPXMock,
) -> None:
    """``project_slug=None`` is forwarded verbatim; daemon picks primary."""
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        json={"text": "ok", "specialist_invoked": None},
    )
    voice = MockVoice(scripted_user_text="hi")
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    daemon = DaemonClient(base_url="http://127.0.0.1:8765")

    await run_one_turn(voice=voice, sm=sm, daemon=daemon)

    sent = httpx_mock.get_request()
    assert sent is not None
    body = json.loads(sent.content)
    assert body["project_slug"] is None


@pytest.mark.asyncio
async def test_run_one_turn_when_muted_is_noop(httpx_mock: HTTPXMock) -> None:
    """If sm.state is MUTED at entry: no voice turn, no daemon call."""
    voice = _SpyMockVoice(scripted_user_text="should not fire")
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    sm.transition(State.MUTED)
    daemon = DaemonClient(base_url="http://127.0.0.1:8765")

    # No httpx_mock.add_response — any HTTP call would fail collection assertion.
    await run_one_turn(voice=voice, sm=sm, daemon=daemon, project_slug="demo")

    assert voice.start_turn_calls == 0
    assert sm.state == State.MUTED
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_run_one_turn_records_final_idle_state(
    httpx_mock: HTTPXMock,
) -> None:
    """After a successful turn the state machine lands in IDLE."""
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        json={"text": "ok", "specialist_invoked": None},
    )
    voice = MockVoice(scripted_user_text="hi")
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    daemon = DaemonClient(base_url="http://127.0.0.1:8765")

    assert sm.state == State.IDLE  # sanity
    await run_one_turn(voice=voice, sm=sm, daemon=daemon)
    assert sm.state == State.IDLE


class _LifecycleTrackingVoice(MockVoice):
    """``MockVoice`` whose ``start_turn`` CM records enter/exit events.

    Used to assert ``run_one_turn`` both enters AND exits the CM, on
    happy and exception paths alike — i.e. the realtime connection is
    released whether the turn succeeds or blows up midway.
    """

    def __init__(self, *, scripted_user_text: str) -> None:
        super().__init__(scripted_user_text=scripted_user_text)
        self.enter_count: int = 0
        self.exit_count: int = 0

    def start_turn(self) -> AbstractAsyncContextManager[MockVoiceTurn]:
        parent = super().start_turn()

        @asynccontextmanager
        async def tracked() -> AsyncIterator[MockVoiceTurn]:
            self.enter_count += 1
            try:
                async with parent as turn:
                    yield turn
            finally:
                self.exit_count += 1

        return tracked()


@pytest.mark.asyncio
async def test_run_one_turn_exits_voice_turn_context_on_happy_path(
    httpx_mock: HTTPXMock,
) -> None:
    """Happy path: ``start_turn`` CM is entered once and exited once."""
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        json={"text": "ok", "specialist_invoked": None},
    )
    voice = _LifecycleTrackingVoice(scripted_user_text="hi")
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    daemon = DaemonClient(base_url="http://127.0.0.1:8765")

    await run_one_turn(voice=voice, sm=sm, daemon=daemon)

    assert voice.enter_count == 1
    assert voice.exit_count == 1


@pytest.mark.asyncio
async def test_run_one_turn_exits_voice_turn_context_on_exception(
    httpx_mock: HTTPXMock,
) -> None:
    """Regression: if the daemon call raises mid-turn, the CM still exits.

    This is the bug I1 is fixing — the previous implementation awaited
    ``voice.start_turn()`` and did nothing to close the connection on
    failure. The context-manager shape makes cleanup structural.
    """
    # Daemon returns a 500; `raise_for_status()` will throw inside the
    # turn body, after start_turn has been entered.
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        status_code=500,
        json={"detail": "boom"},
    )
    voice = _LifecycleTrackingVoice(scripted_user_text="hi")
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    daemon = DaemonClient(base_url="http://127.0.0.1:8765")

    with pytest.raises(httpx.HTTPStatusError):
        await run_one_turn(voice=voice, sm=sm, daemon=daemon)

    # Even though the turn raised, the CM must have exited cleanly.
    assert voice.enter_count == 1
    assert voice.exit_count == 1


class _RaisingGetUserTextTurn(MockVoiceTurn):
    """Turn whose ``get_user_text`` always raises."""

    def __init__(self) -> None:
        super().__init__(user_text="")

    async def get_user_text(self) -> str:
        raise RuntimeError("transcription exploded")


class _RaisingGetUserTextVoice(MockVoice):
    """Voice whose turn raises from ``get_user_text`` (LISTENING-state raise)."""

    def start_turn(self) -> AbstractAsyncContextManager[MockVoiceTurn]:
        @asynccontextmanager
        async def _cm() -> AsyncIterator[MockVoiceTurn]:
            yield _RaisingGetUserTextTurn()

        return _cm()


class _RaisingSpeakTextTurn(MockVoiceTurn):
    """Turn whose ``speak_text`` raises (so ``get_user_text`` still succeeds)."""

    def __init__(self, user_text: str) -> None:
        super().__init__(user_text=user_text)

    async def speak_text(self, text: str) -> None:
        raise RuntimeError("tts exploded")


class _RaisingSpeakTextVoice(MockVoice):
    """Voice whose turn raises from ``speak_text`` (LISTENING-again-state raise)."""

    def start_turn(self) -> AbstractAsyncContextManager[MockVoiceTurn]:
        user_text = self._user_text

        @asynccontextmanager
        async def _cm() -> AsyncIterator[MockVoiceTurn]:
            yield _RaisingSpeakTextTurn(user_text)

        return _cm()


@pytest.mark.asyncio
async def test_run_one_turn_resets_to_idle_when_get_user_text_raises(
    httpx_mock: HTTPXMock,
) -> None:
    """If ``get_user_text`` raises in LISTENING: state lands back at IDLE.

    Guards against the robot's LISTENING posture persisting forever after
    a transcription failure.
    """
    # No httpx_mock.add_response — the turn must raise before the brain call.
    voice = _RaisingGetUserTextVoice(scripted_user_text="unused")
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    daemon = DaemonClient(base_url="http://127.0.0.1:8765")

    with pytest.raises(RuntimeError, match="transcription exploded"):
        await run_one_turn(voice=voice, sm=sm, daemon=daemon)

    assert sm.state == State.IDLE
    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_run_one_turn_resets_to_idle_when_brain_query_raises(
    httpx_mock: HTTPXMock,
) -> None:
    """If ``brain_query`` raises in THINKING: state lands back at IDLE.

    Previously the state machine would stay stuck at THINKING after any
    daemon failure.
    """
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        status_code=500,
        json={"detail": "brain down"},
    )
    voice = MockVoice(scripted_user_text="hi")
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    daemon = DaemonClient(base_url="http://127.0.0.1:8765")

    with pytest.raises(httpx.HTTPStatusError):
        await run_one_turn(voice=voice, sm=sm, daemon=daemon)

    assert sm.state == State.IDLE


@pytest.mark.asyncio
async def test_run_one_turn_resets_to_idle_when_speak_text_raises(
    httpx_mock: HTTPXMock,
) -> None:
    """If ``speak_text`` raises in LISTENING (reply): state lands back at IDLE."""
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        json={"text": "ok", "specialist_invoked": None},
    )
    voice = _RaisingSpeakTextVoice(scripted_user_text="hi")
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    daemon = DaemonClient(base_url="http://127.0.0.1:8765")

    with pytest.raises(RuntimeError, match="tts exploded"):
        await run_one_turn(voice=voice, sm=sm, daemon=daemon)

    assert sm.state == State.IDLE
