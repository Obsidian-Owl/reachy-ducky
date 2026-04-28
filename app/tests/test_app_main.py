"""Tests for :mod:`reachy_ducky_app.main` — the Reachy-side entry point.

``reachy_mini`` is now a plain base dep (see ``app/pyproject.toml`` and
the workspace-root ``[tool.uv] dependency-metadata`` patch for
``gstreamer-msvc-runtime``), so it installs on Linux CI and macOS dev
venvs alike. These tests still avoid constructing a real ``ReachyMini``
(which would spin up WebRTC media pipelines) and verify the app's
construction path via a fake ``reachy_mini_app`` stand-in.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from reachy_ducky_app.embodiment import MockMotionDriver
from reachy_ducky_app.main import ReachyDuckyApp, main
from reachy_ducky_app.mute import MuteGate
from reachy_ducky_app.voice.audio_io import (
    AudioFrame,
    MicSource,
    MockMicSource,
    MockSpeakerSink,
    SpeakerSink,
)
from reachy_ducky_app.wake import MockWakeDetector


def test_reachy_ducky_app_imports_cleanly() -> None:
    """``ReachyDuckyApp`` + ``main`` import cleanly on dev venvs.

    ``reachy_mini`` is a plain base dep, but module-load must not trigger
    heavy WebRTC / media-pipeline imports (structural-shape pattern in
    ``main.py``).
    """
    assert ReachyDuckyApp is not None
    assert callable(main)


def test_wake_triggered_placeholder_returns_false() -> None:
    """Baseline lock: the Phase A ``_wake_triggered`` stub always returns False."""
    app = ReachyDuckyApp()
    assert app._wake_triggered() is False


async def test_run_async_exits_immediately_when_stop_event_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-set stop_event: ``_run_async`` must return without running any turn."""
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("REACHY_DUCKY_WAKE_MOCK", "1")

    run_one_turn_calls: list[object] = []

    async def _spy_run_one_turn(**kwargs: object) -> None:
        run_one_turn_calls.append(kwargs)

    monkeypatch.setattr("reachy_ducky_app.main.run_one_turn", _spy_run_one_turn)

    app = ReachyDuckyApp()
    stop = threading.Event()
    stop.set()

    await asyncio.wait_for(
        app._run_async(reachy_mini=object(), stop_event=stop),
        timeout=0.5,
    )

    assert run_one_turn_calls == []


async def test_wake_event_drives_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_run_async`` awaits the wake Event — setting it triggers exactly one turn.

    The event-driven contract: the loop blocks on ``wake.event.wait()`` via
    ``asyncio.wait(FIRST_COMPLETED)``. Firing the event once must drive
    exactly one ``run_one_turn``, not be busy-polled.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    run_one_turn_calls: list[dict[str, object]] = []
    stop = threading.Event()

    injected_wake = MockWakeDetector()

    async def _spy_run_one_turn(**kwargs: object) -> None:
        run_one_turn_calls.append(kwargs)
        # One turn is enough; set stop so the loop exits on the next cycle.
        stop.set()

    monkeypatch.setattr("reachy_ducky_app.main.run_one_turn", _spy_run_one_turn)
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_wake_detector",
        lambda: injected_wake,
    )
    # Pattern C: the wake pump pulls from the mic and the LISTENING
    # transition drives the motion driver. Use the silent MockMicSource
    # and the no-op MockMotionDriver so we exercise main()'s wiring
    # without standing up the real Reachy media stack.
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_mic_source",
        lambda **_: MockMicSource(),
    )
    monkeypatch.setattr(
        "reachy_ducky_app.main.ReachyMotionDriver",
        lambda _reachy_mini: MockMotionDriver(),
    )

    async def _fire_wake_soon() -> None:
        # Let _run_async reach its asyncio.wait; then set the event.
        await asyncio.sleep(0)
        injected_wake.event.set()

    app = ReachyDuckyApp()

    async with asyncio.TaskGroup() as tg:
        tg.create_task(_fire_wake_soon())
        tg.create_task(
            asyncio.wait_for(
                app._run_async(reachy_mini=object(), stop_event=stop),
                timeout=1.0,
            )
        )

    assert len(run_one_turn_calls) == 1
    assert "voice" in run_one_turn_calls[0]
    assert "sm" in run_one_turn_calls[0]
    assert "daemon" in run_one_turn_calls[0]
    assert run_one_turn_calls[0]["project_slug"] is None


async def test_stop_wins_when_wake_and_stop_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When wake.event and stop_event both fire before the loop re-checks,
    stop wins and no additional turn runs.

    Pins the precedence so a future refactor doesn't accidentally let a
    race cause a turn to fire during shutdown. Pre-setting both events
    before ``_run_async`` starts is the cleanest way to exercise the
    decision point: the outer ``while not stop_event.is_set()`` guard
    must short-circuit regardless of the wake event's state.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    run_one_turn_calls: list[object] = []

    async def _spy_run_one_turn(**kwargs: object) -> None:
        run_one_turn_calls.append(kwargs)

    injected_wake = MockWakeDetector()
    injected_wake.event.set()

    monkeypatch.setattr("reachy_ducky_app.main.run_one_turn", _spy_run_one_turn)
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_wake_detector",
        lambda: injected_wake,
    )

    app = ReachyDuckyApp()
    stop = threading.Event()
    stop.set()

    await asyncio.wait_for(
        app._run_async(reachy_mini=object(), stop_event=stop),
        timeout=0.5,
    )

    assert run_one_turn_calls == []


async def test_wake_loop_exits_cleanly_without_firing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop event causes clean exit when the wake event never fires.

    Verifies the stop-bridge wins when ``wake.event`` stays unset: zero
    turns run, and ``_run_async`` returns promptly after ``stop_event.set()``.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    run_one_turn_calls: list[object] = []

    async def _spy_run_one_turn(**kwargs: object) -> None:
        run_one_turn_calls.append(kwargs)

    injected_wake = MockWakeDetector()

    monkeypatch.setattr("reachy_ducky_app.main.run_one_turn", _spy_run_one_turn)
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_wake_detector",
        lambda: injected_wake,
    )
    # Pattern C: wake pump consumes mic frames during Phase 1. Inject the
    # silent MockMicSource so this test (which never fires a wake event)
    # doesn't touch the real Reachy media stack.
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_mic_source",
        lambda **_: MockMicSource(),
    )

    stop = threading.Event()

    async def _stop_soon() -> None:
        await asyncio.sleep(0.3)  # 3x the 0.1s stop-bridge tick; margin for loaded CI
        stop.set()

    app = ReachyDuckyApp()

    async with asyncio.TaskGroup() as tg:
        tg.create_task(_stop_soon())
        tg.create_task(
            asyncio.wait_for(
                app._run_async(reachy_mini=object(), stop_event=stop),
                timeout=1.0,
            )
        )

    assert run_one_turn_calls == []
    assert not injected_wake.event.is_set()


def test_main_exits_with_stopped_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main()`` pre-sets the stop event and returns cleanly (no hang)."""
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("REACHY_DUCKY_WAKE_MOCK", "1")
    # If main() hangs, pytest's per-test timeout (or CI watchdog) will catch
    # it; belt-and-braces, the stop_event.set() inside main() guarantees the
    # loop body is skipped, so this returns synchronously.
    main()


def test_load_dotenv_reads_key_value_lines_without_overwriting_existing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_load_dotenv`` parses KEY=VALUE lines, skips comments, no overwrite."""
    from reachy_ducky_app.main import _load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\n"
        "\n"
        "OPENAI_API_KEY=sk-from-file\n"
        'GITHUB_PERSONAL_ACCESS_TOKEN="ghp_quoted"\n'
        "REACHY_DUCKY_AUTH_TOKEN=existing_should_not_overwrite\n"
    )

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_PERSONAL_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("REACHY_DUCKY_AUTH_TOKEN", "process_env_wins")

    loaded = _load_dotenv(env_file)
    assert loaded == 2  # OpenAI + GH; auth-token already in env
    assert os.environ["OPENAI_API_KEY"] == "sk-from-file"
    assert os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_quoted"
    assert os.environ["REACHY_DUCKY_AUTH_TOKEN"] == "process_env_wins"


def test_load_dotenv_returns_zero_when_file_missing(tmp_path: Path) -> None:
    """No .env file is fine — load_dotenv returns 0 silently."""
    from reachy_ducky_app.main import _load_dotenv

    assert _load_dotenv(tmp_path / "nonexistent.env") == 0


async def test_run_async_closes_daemon_client_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On shutdown, the app calls ``daemon.aclose()`` to release the pool.

    Regression for bug I2: pools the httpx.AsyncClient across calls, so
    it must be drained on stop. We mock ``DaemonClient.from_env`` to
    return a spy whose ``aclose`` is an :class:`AsyncMock`, immediately
    stop the event, and assert ``aclose`` was awaited exactly once.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("REACHY_DUCKY_WAKE_MOCK", "1")

    fake_daemon = MagicMock()
    fake_daemon.aclose = AsyncMock()
    monkeypatch.setattr(
        "reachy_ducky_app.main.DaemonClient.from_env",
        MagicMock(return_value=fake_daemon),
    )

    app = ReachyDuckyApp()
    stop = threading.Event()
    stop.set()

    await asyncio.wait_for(
        app._run_async(reachy_mini=object(), stop_event=stop),
        timeout=0.5,
    )

    fake_daemon.aclose.assert_awaited_once()


async def test_run_async_constructs_voice_with_default_mic_and_speaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app wires the default audio_io factories into OpenAIRealtimeVoice.

    Guards the commit-3 wiring: ``_run_async`` must call
    :func:`load_default_mic_source` and :func:`load_default_speaker_sink`
    rather than hard-coding Mock impls directly. When the hardware-backed
    factory bodies land, ``main.py`` should require zero changes.

    Also pins Task A.2: the mic factory receives the SAME ``MuteGate``
    instance that the state machine received, so a MUTED transition
    synchronously affects the mic pump. The speaker factory deliberately
    does NOT receive a gate — speaker output is LLM-generated and
    shouldn't be silenced when the user mutes.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("REACHY_DUCKY_WAKE_MOCK", "1")

    mic_factory_kwargs: list[dict[str, object]] = []
    speaker_factory_kwargs: list[dict[str, object]] = []

    def _spy_mic_factory(
        *,
        reachy_mini: object | None = None,
        mute_gate: MuteGate | None = None,
    ) -> MicSource:
        mic_factory_kwargs.append({"reachy_mini": reachy_mini, "mute_gate": mute_gate})
        return MockMicSource()

    def _spy_speaker_factory(*, reachy_mini: object | None = None) -> SpeakerSink:
        speaker_factory_kwargs.append({"reachy_mini": reachy_mini})
        return MockSpeakerSink()

    sm_kwargs: list[dict[str, object]] = []

    def _spy_sm(**kwargs: object) -> MagicMock:
        sm_kwargs.append(kwargs)
        return MagicMock()

    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_mic_source",
        _spy_mic_factory,
    )
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_speaker_sink",
        _spy_speaker_factory,
    )
    monkeypatch.setattr(
        "reachy_ducky_app.main.EmbodimentStateMachine",
        _spy_sm,
    )

    app = ReachyDuckyApp()
    stop = threading.Event()
    stop.set()

    await asyncio.wait_for(
        app._run_async(reachy_mini=object(), stop_event=stop),
        timeout=0.5,
    )

    assert len(mic_factory_kwargs) == 1
    assert len(speaker_factory_kwargs) == 1
    assert len(sm_kwargs) == 1
    # Mic factory MUST have received a real MuteGate, and it MUST be the
    # SAME instance that the state machine received. If main() ever
    # constructs two separate gates, the (gate, mic-pump) invariant
    # silently breaks: a MUTED transition on the SM would toggle one gate
    # while the mic pump reads the other.
    mic_gate = mic_factory_kwargs[0]["mute_gate"]
    sm_gate = sm_kwargs[0]["mute_gate"]
    assert isinstance(mic_gate, MuteGate)
    assert mic_gate is sm_gate


async def test_run_async_closes_daemon_client_even_when_turn_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``run_one_turn`` raises, ``daemon.aclose()`` must still be awaited.

    Guards that the ``try/finally`` wraps the whole wake loop, so the
    pool is drained on crash as well as clean shutdown. Drives a turn
    via a wake detector that fires on the first ``feed`` call so the
    pump immediately yields to Phase 2.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    fake_daemon = MagicMock()
    fake_daemon.aclose = AsyncMock()
    monkeypatch.setattr(
        "reachy_ducky_app.main.DaemonClient.from_env",
        MagicMock(return_value=fake_daemon),
    )

    class _BoomError(RuntimeError):
        pass

    async def _blowing_up(**kwargs: object) -> None:
        raise _BoomError("turn failed")

    monkeypatch.setattr("reachy_ducky_app.main.run_one_turn", _blowing_up)

    # ``trigger_on_feed=True`` makes the detector fire on the first
    # ``feed`` call, which the wake pump will issue as soon as the mic
    # yields a frame. Avoids racing the pre-set-then-reset() pattern.
    injected_wake = MockWakeDetector(trigger_on_feed=True)
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_wake_detector",
        lambda: injected_wake,
    )

    class _OneFrameMic(MockMicSource):
        async def frames(self) -> AsyncIterator[AudioFrame]:
            yield (24_000, np.zeros(960, dtype=np.int16))

    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_mic_source",
        lambda **_: _OneFrameMic(),
    )
    monkeypatch.setattr(
        "reachy_ducky_app.main.ReachyMotionDriver",
        lambda _reachy_mini: MockMotionDriver(),
    )

    stop = threading.Event()
    app = ReachyDuckyApp()

    with pytest.raises(_BoomError):
        await asyncio.wait_for(
            app._run_async(reachy_mini=object(), stop_event=stop),
            timeout=0.5,
        )

    fake_daemon.aclose.assert_awaited_once()
