"""Tests for :func:`run_one_turn` — the single-turn conversation orchestrator.

Integration-style unit tests. Each test composes the real voice mock,
the real motion-driver mock, the real state machine, and a
pytest-httpx-mocked :class:`DaemonClient`. No network, no hardware.
"""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock
from reachy_ducky_app.conversation import run_one_turn
from reachy_ducky_app.daemon_client import DaemonClient
from reachy_ducky_app.embodiment.motion_driver import MockMotionDriver
from reachy_ducky_app.embodiment.state_machine import EmbodimentStateMachine
from reachy_ducky_app.voice.interface import VoiceTurn
from reachy_ducky_app.voice.mock import MockVoice, MockVoiceTurn
from reachy_ducky_protocol.messages import State


class _SpyMockVoice(MockVoice):
    """``MockVoice`` that counts ``start_turn`` calls for no-op assertions."""

    def __init__(self, *, scripted_user_text: str) -> None:
        super().__init__(scripted_user_text=scripted_user_text)
        self.start_turn_calls: int = 0

    async def start_turn(self) -> VoiceTurn:
        self.start_turn_calls += 1
        return await super().start_turn()


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

    # Capture the turn by patching start_turn so we can inspect spoken_texts.
    captured: list[VoiceTurn] = []
    original_start = voice.start_turn

    async def spy_start() -> VoiceTurn:
        turn = await original_start()
        captured.append(turn)
        return turn

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
